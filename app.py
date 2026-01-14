# streamlit_app.py
# -*- coding: utf-8 -*-
# Govable AI Bureau - One-Stop (Citations + Flow + Coordinate Form Fill)
# Last updated: 2026-01-14 (KST)

"""
✅ 이 버전에서 바뀐 핵심 (요구사항 반영)
1) “근거”는 무조건 Citation 객체로 강제 → UI에서 클릭하면 원문 이동(법령/행정규칙/뉴스)
2) “처리 방향”은 글이 아니라 구조화(JSON): 처리흐름(Flow) / 핵심(Key) / 주의(Risk) / 근거(Citations)
3) “서식 채우기”는 추출이 아니라 좌표(Bounding Box)만 저장 → PDF 오버레이 생성(옵션 A)
4) optional deps 미설치여도 앱이 죽지 않도록 방어(요청/배포 환경 차이 고려)
"""

import json
import re
import time
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import escape as _escape
from typing import Optional, Dict, Any, List, Tuple

import streamlit as st

# ---------------------------
# Optional deps (앱 전체가 죽지 않도록)
# ---------------------------
try:
    import requests
except Exception:
    requests = None

try:
    from groq import Groq
except Exception:
    Groq = None

try:
    from supabase import create_client
    from supabase.lib.client_options import ClientOptions
except Exception:
    create_client = None
    ClientOptions = None

try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleAuthRequest
except Exception:
    service_account = None
    GoogleAuthRequest = None

# PDF overlay optional deps
try:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
except Exception:
    rl_canvas = None
    A4 = None
    mm = None

try:
    from pypdf import PdfReader, PdfWriter
except Exception:
    PdfReader = None
    PdfWriter = None

try:
    import io
except Exception:
    io = None

# ==========================================
# 0) Settings
# ==========================================
MAX_FOLLOWUP_Q = 5
LAW_MAX_WORKERS = 3
HTTP_RETRIES = 2
HTTP_TIMEOUT = 12
VERTEX_TIMEOUT = 60  # cold start 대비
KST = timezone(timedelta(hours=9))
KOREA_DOMAIN = "@korea.kr"

_vertex_lock = threading.Lock()


def _safe_secrets(section: str) -> dict:
    """secrets.toml이 아예 없어도 에러 없이 빈 dict 반환"""
    try:
        return dict(st.secrets.get(section, {}))
    except Exception:
        return {}


# ==========================================
# 1) Configuration & Styles (안전한 CSS만)
# ==========================================
st.set_page_config(layout="wide", page_title="AI Bureau: The Legal Glass", page_icon="⚖️")

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

.stApp{
  background: linear-gradient(135deg, #f0f4f8 0%, #e1e8ed 50%, #d4dce3 100%);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

[data-testid="stSidebar"]{
  background: rgba(255,255,255,0.92);
  backdrop-filter: blur(18px);
  border-right: 1px solid rgba(99,102,241,0.18);
}

.paper-sheet{
  background: rgba(255,255,255,0.97);
  width: 100%;
  max-width: 210mm;
  min-height: 297mm;
  padding: 22mm;
  margin: auto;
  box-shadow: 0 20px 60px rgba(0,0,0,0.12);
  border: 1px solid rgba(0,0,0,0.08);
  font-family: 'Inter', sans-serif;
  color: #111827;
  line-height: 1.8;
  position: relative;
  border-radius: 16px;
}

.doc-header{
  text-align:center;
  font-size: 22pt;
  font-weight: 900;
  margin-bottom: 22px;
}

.doc-info{
  display:flex;
  justify-content:space-between;
  font-size: 10.5pt;
  border-bottom: 1px solid rgba(17,24,39,0.25);
  padding-bottom: 10px;
  margin-bottom: 18px;
  gap: 12px;
  flex-wrap: wrap;
}

.doc-body{ font-size: 11.2pt; white-space: pre-line; }
.doc-footer{ text-align:center; font-size: 16pt; font-weight: 800; margin-top: 60px; letter-spacing: 3px; }

.stamp{
  position:absolute;
  bottom: 75px;
  right: 70px;
  border: 3px solid #dc2626;
  color: #dc2626;
  padding: 8px 14px;
  font-size: 13pt;
  font-weight: 900;
  transform: rotate(-13deg);
  border-radius: 10px;
  background: rgba(255,255,255,0.94);
}

.agent-log{
  font-family: 'Inter', 'Consolas', monospace;
  font-size: 0.92rem;
  padding: 12px 16px;
  border-radius: 12px;
  margin-bottom: 10px;
  border-left: 4px solid rgba(99,102,241,0.8);
  background: rgba(99,102,241,0.08);
}

.log-legal{ border-left-color:#667eea; background: rgba(102,126,234,0.10); }
.log-search{ border-left-color:#4facfe; background: rgba(79,172,254,0.10); }
.log-strat{ border-left-color:#a855f7; background: rgba(168,85,247,0.10); }
.log-calc{ border-left-color:#22c55e; background: rgba(34,197,94,0.10); }
.log-draft{ border-left-color:#fb7185; background: rgba(251,113,133,0.10); }
.log-sys{ border-left-color:#94a3b8; background: rgba(148,163,184,0.10); }

.stButton>button{
  background: linear-gradient(135deg,#667eea 0%, #764ba2 100%);
  color: white;
  border: 0;
  border-radius: 12px;
  padding: 0.85rem 1.4rem;
  font-weight: 800;
  box-shadow: 0 10px 30px rgba(102,126,234,0.28);
}
.stButton>button:hover{ transform: translateY(-2px); }

.stTextInput>div>div>input, .stTextArea>div>div>textarea{
  border: 1px solid rgba(99,102,241,0.25);
  border-radius: 12px;
  padding: 0.85rem 1rem;
  background: rgba(255,255,255,0.95);
}

header { height:0px !important; }
footer { display:none !important; }
</style>
""",
    unsafe_allow_html=True,
)

# ==========================================
# 2) Utils (HTTP, Decode, XML)
# ==========================================
def _require_requests():
    if requests is None:
        raise RuntimeError("requests 패키지 미설치. requirements.txt 확인 필요.")


def http_get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
             timeout: int = HTTP_TIMEOUT, retries: int = HTTP_RETRIES):
    _require_requests()
    last_err = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            if i < retries:
                time.sleep(0.3 * (2 ** i))
    raise RuntimeError(f"HTTP GET 실패: {last_err}")


def http_post(url: str, json_body: dict, headers: Optional[dict] = None,
              timeout: int = HTTP_TIMEOUT, retries: int = HTTP_RETRIES):
    _require_requests()
    last_err = None
    for i in range(retries + 1):
        try:
            r = requests.post(url, json=json_body, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            if i < retries:
                time.sleep(0.3 * (2 ** i))
    raise RuntimeError(f"HTTP POST 실패: {last_err}")


def _safe_decode(b: bytes) -> str:
    """UTF-8 우선, 실패 시 EUC-KR 시도"""
    for enc in ["utf-8", "euc-kr", "cp949"]:
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", errors="ignore")


def _safe_et_from_bytes(b: bytes) -> ET.Element:
    """XML 파싱 (인코딩 자동 감지)"""
    text = _safe_decode(b)
    try:
        return ET.fromstring(text)
    except Exception:
        cleaned = re.sub(r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]", "", text)
        return ET.fromstring(cleaned)


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text)


def _now_kst() -> datetime:
    return datetime.now(KST)


def _safe_int(x, default=0) -> int:
    try:
        return int(str(x).strip())
    except Exception:
        return default


# ==========================================
# 3) Cached Calls (법령/행정규칙/AI search/뉴스)
# ==========================================
@st.cache_data(ttl=86400, show_spinner=False)
def cached_law_search(api_id: str, law_name: str) -> str:
    base_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {"OC": api_id, "target": "law", "type": "XML", "query": law_name, "display": 1}
    r = http_get(base_url, params=params, timeout=10)
    root = _safe_et_from_bytes(r.content)
    law_node = root.find(".//law")
    if law_node is None:
        return ""
    return (law_node.findtext("법령일련번호") or "").strip()


@st.cache_data(ttl=86400, show_spinner=False)
def cached_law_detail_xml(api_id: str, mst_id: str) -> str:
    service_url = "https://www.law.go.kr/DRF/lawService.do"
    params = {"OC": api_id, "target": "law", "type": "XML", "MST": mst_id}
    r = http_get(service_url, params=params, timeout=15)
    return _safe_decode(r.content)


@st.cache_data(ttl=86400, show_spinner=False)
def cached_admrul_search(api_id: str, query: str) -> str:
    base_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {"OC": api_id, "target": "admrul", "type": "XML", "query": query, "display": 1}
    r = http_get(base_url, params=params, timeout=10)
    root = _safe_et_from_bytes(r.content)
    node = root.find(".//admrul")
    if node is None:
        return ""
    return (node.findtext("행정규칙ID") or node.findtext("admrulId") or "").strip()


@st.cache_data(ttl=86400, show_spinner=False)
def cached_admrul_detail(api_id: str, admrul_id: str) -> str:
    service_url = "https://www.law.go.kr/DRF/lawService.do"
    params = {"OC": api_id, "target": "admrul", "type": "XML", "ID": admrul_id}
    r = http_get(service_url, params=params, timeout=15)
    return _safe_decode(r.content)


@st.cache_data(ttl=600, show_spinner=False)
def cached_ai_search(api_id: str, query: str, top_k: int = 5) -> List[Dict[str, str]]:
    base_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {"OC": api_id, "target": "aiSearch", "type": "XML", "query": query, "display": top_k}
    try:
        r = http_get(base_url, params=params, timeout=12)
        root = _safe_et_from_bytes(r.content)
        results = []
        for item in root.findall(".//law") or root.findall(".//search") or root.findall(".//item"):
            title = (item.findtext("법령명") or item.findtext("제목") or item.findtext("title") or "").strip()
            link = (item.findtext("법령링크") or item.findtext("link") or "").strip()
            doc_type = (item.findtext("법령구분") or item.findtext("type") or "법령").strip()
            if title:
                results.append({"title": title, "link": link, "type": doc_type})
        return results
    except Exception:
        return []


@st.cache_data(ttl=600, show_spinner=False)
def cached_naver_news(query: str, top_k: int = 3) -> List[Dict[str, str]]:
    g = _safe_secrets("general")
    client_id = g.get("NAVER_CLIENT_ID")
    client_secret = g.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret or not query:
        return []

    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    params = {"query": query, "display": 10, "sort": "date"}
    r = http_get("https://openapi.naver.com/v1/search/news.json", params=params, headers=headers, timeout=8)
    items = r.json().get("items", []) or []

    def clean_html(s: str) -> str:
        s = re.sub(r"<[^>]+>", "", s or "")
        return s.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()

    out = []
    for it in items[:top_k]:
        out.append({
            "title": clean_html(it.get("title", "")),
            "desc": clean_html(it.get("description", ""))[:180],
            "url": it.get("link", ""),
            "published_at": it.get("pubDate", ""),
            "type": "NEWS"
        })
    return out


# ==========================================
# 4) Core Schemas (Citations / CasePlan / DocDraft / FormTemplate)
# ==========================================
def citation_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "type": {"type": "string"},  # LAW / ADMRUL / NEWS / ETC
            "url": {"type": "string"},
            "note": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["title", "type", "url"]
    }


CASE_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "case_type": {"type": "string"},
        "flow_steps": {"type": "array", "items": {"type": "string"}},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "citations": {"type": "array", "items": citation_schema()},
    },
    "required": ["case_type", "flow_steps", "key_points", "risks", "citations"],
}

DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "receiver": {"type": "string"},
        "body_paragraphs": {"type": "array", "items": {"type": "string"}},
        "department_head": {"type": "string"},
    },
    "required": ["title", "receiver", "body_paragraphs", "department_head"],
}

FORM_TEMPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "template_id": {"type": "string"},
        "name": {"type": "string"},
        "page_size": {"type": "string"},  # A4 등
        "unit": {"type": "string"},       # "pt" or "mm"
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "label": {"type": "string"},
                    "page": {"type": "integer"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "w": {"type": "number"},
                    "h": {"type": "number"},
                    "font": {"type": "string"},
                    "size": {"type": "number"},
                },
                "required": ["key", "label", "page", "x", "y", "w", "h"]
            }
        }
    },
    "required": ["template_id", "name", "page_size", "unit", "fields"]
}


# ==========================================
# 5) Infrastructure Services
# ==========================================
def _vertex_schema_from_doc_schema(doc_schema: Optional[dict]) -> Optional[dict]:
    if not doc_schema or not isinstance(doc_schema, dict):
        return None

    def norm_type(t):
        if not t:
            return None
        mapping = {"object": "object", "array": "array", "string": "string",
                   "integer": "integer", "number": "number", "boolean": "boolean"}
        return mapping.get(str(t).lower().strip(), str(t).lower())

    def walk(s):
        if isinstance(s, dict):
            out = {}
            if "type" in s:
                out["type"] = norm_type(s.get("type")) or "object"
            for k, v in s.items():
                if k == "type":
                    continue
                if k in ("properties", "items"):
                    out[k] = walk(v)
                elif k == "required" and isinstance(v, list):
                    out[k] = v
                else:
                    out[k] = walk(v)
            return out
        if isinstance(s, list):
            return [walk(x) for x in s]
        return s

    return walk(doc_schema)


class LLMService:
    """Vertex AI (Gemini) + Groq 백업"""

    def __init__(self):
        g = _safe_secrets("general")
        v = _safe_secrets("vertex")

        self.groq_key = g.get("GROQ_API_KEY")
        self.project_id = v.get("PROJECT_ID")
        self.location = v.get("LOCATION", "asia-northeast3")
        self.vertex_models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash-001"]
        self.groq_models = ["llama-3.3-70b-versatile", "llama3-70b-8192"]

        self.creds = None
        sa_raw = v.get("SERVICE_ACCOUNT_JSON")
        if sa_raw and service_account is not None:
            try:
                sa_info = json.loads(sa_raw) if isinstance(sa_raw, str) else sa_raw
                self.creds = service_account.Credentials.from_service_account_info(
                    sa_info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
            except Exception:
                self.creds = None

        self.groq_client = Groq(api_key=self.groq_key) if (Groq and self.groq_key) else None

    def _refresh_creds_safe(self):
        with _vertex_lock:
            if self.creds and (not self.creds.valid or self.creds.expired):
                try:
                    self.creds.refresh(GoogleAuthRequest())
                except Exception:
                    pass

    def _vertex_generate(self, prompt: str, model_name: str,
                         response_mime_type: Optional[str] = None,
                         response_schema: Optional[dict] = None) -> str:
        if not (self.creds and self.project_id and self.location and GoogleAuthRequest):
            raise RuntimeError("Vertex AI 미설정")

        self._refresh_creds_safe()

        model_path = f"projects/{self.project_id}/locations/{self.location}/publishers/google/models/{model_name}"
        url = f"https://aiplatform.googleapis.com/v1/{model_path}:generateContent"

        gen_cfg: Dict[str, Any] = {"temperature": 0.2, "maxOutputTokens": 2048}
        if response_mime_type:
            gen_cfg["responseMimeType"] = response_mime_type
        if response_schema:
            gen_cfg["responseSchema"] = response_schema

        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
        headers = {"Authorization": f"Bearer {self.creds.token}", "Content-Type": "application/json"}

        r = http_post(url, json_body=payload, headers=headers, timeout=VERTEX_TIMEOUT, retries=1)
        data = r.json()

        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(data["error"].get("message", "Vertex error"))

        try:
            return data["candidates"][0]["content"]["parts"][0].get("text", "") or ""
        except Exception:
            return ""

    def _generate_groq(self, prompt: str) -> str:
        if not self.groq_client:
            return ""
        for model in self.groq_models:
            try:
                completion = self.groq_client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}], temperature=0.1
                )
                return completion.choices[0].message.content or ""
            except Exception:
                continue
        return ""

    def generate_text(self, prompt: str) -> str:
        for m in self.vertex_models:
            try:
                out = self._vertex_generate(prompt, m)
                if out and out.strip():
                    return out
            except Exception:
                continue
        out = self._generate_groq(prompt)
        if out and out.strip():
            return out
        return "⚠️ LLM 연결 실패 (Vertex/Groq 설정 확인)"

    def generate_json(self, prompt: str, schema: Optional[dict] = None) -> Optional[dict]:
        response_schema = _vertex_schema_from_doc_schema(schema)

        for m in self.vertex_models:
            try:
                txt = (self._vertex_generate(prompt, m, "application/json", response_schema) or "").strip()
                if txt:
                    return json.loads(txt)
            except Exception:
                continue

        def _try_parse(txt: str) -> Optional[dict]:
            txt = (txt or "").strip()
            if not txt:
                return None
            try:
                return json.loads(txt)
            except Exception:
                pass
            try:
                match = re.search(r"\{.*\}|\[.*\]", txt, re.DOTALL)
                return json.loads(match.group(0)) if match else None
            except Exception:
                return None

        for attempt in range(2):
            suffix = "\n\n반드시 JSON만 출력." if attempt == 0 else "\n\n순수 JSON 외의 문자 금지."
            txt = self.generate_text(prompt + suffix)
            j = _try_parse(txt)
            if j is not None:
                return j

        return None


class SearchService:
    """뉴스 검색(네이버 API) → Citation 형태로 반환"""

    def _extract_keywords_llm(self, situation: str) -> str:
        prompt = f"상황: '{situation}'\n뉴스 검색 키워드 2개만 콤마로 구분 출력. (예: 무단방치차량, 과태료)"
        res = (llm_service.generate_text(prompt) or "").strip()
        res = re.sub(r'[".?]', "", res)
        return res[:80] if res else situation[:20]

    def search_news_citations(self, query: str, top_k: int = 3) -> List[dict]:
        items = cached_naver_news(query=query, top_k=top_k)
        out = []
        for it in items:
            if it.get("url"):
                out.append({
                    "title": it.get("title", "뉴스"),
                    "type": "NEWS",
                    "url": it.get("url"),
                    "note": it.get("desc", ""),
                    "confidence": 0.7
                })
        return out

    def search_precedents(self, situation: str, top_k: int = 3) -> Tuple[str, List[dict]]:
        keywords = self._extract_keywords_llm(situation)
        cites = self.search_news_citations(keywords, top_k=top_k)
        if not cites:
            return f"🔍 `{keywords}` 관련 최신 뉴스가 없습니다(또는 API 미설정).", []
        # 텍스트 패널용
        lines = [f"📰 **최신 뉴스 (검색어: {keywords})**", "---"]
        for c in cites:
            lines.append(f"- **[{c['title']}]({c['url']})**\n  : {c.get('note','')}")
        return "\n".join(lines), cites


class DatabaseService:
    """Supabase Auth + DB (supabase-py 2.x 호환)"""

    def __init__(self):
        s = _safe_secrets("supabase")
        self.url = s.get("SUPABASE_URL")
        self.anon_key = s.get("SUPABASE_ANON_KEY") or s.get("SUPABASE_KEY")
        self.service_key = s.get("SUPABASE_SERVICE_ROLE_KEY")

        self.is_active = False
        self.auth_client = None
        self.admin_client = None

        if create_client is None:
            return

        try:
            if self.url and self.anon_key:
                self.auth_client = create_client(self.url, self.anon_key)
                if self.service_key:
                    self.admin_client = create_client(self.url, self.service_key)
                self.is_active = True
        except Exception:
            self.is_active = False

    def is_logged_in(self) -> bool:
        return bool(st.session_state.get("sb_access_token") and st.session_state.get("sb_user_email"))

    def sign_in(self, email: str, password: str) -> dict:
        if not self.is_active or not self.auth_client:
            return {"ok": False, "msg": "Supabase 연결 실패"}
        try:
            resp = self.auth_client.auth.sign_in_with_password({"email": email, "password": password})
            session = getattr(resp, "session", None)
            user = getattr(resp, "user", None)

            access_token = getattr(session, "access_token", None) if session else None
            user_email = getattr(user, "email", None) if user else None
            user_id = getattr(user, "id", None) if user else None

            if not access_token or not user_email:
                return {"ok": False, "msg": "로그인 응답 파싱 실패"}

            st.session_state["sb_access_token"] = access_token
            st.session_state["sb_refresh_token"] = getattr(session, "refresh_token", "") if session else ""
            st.session_state["sb_user_email"] = user_email
            st.session_state["sb_user_id"] = user_id or ""
            return {"ok": True, "msg": "로그인 성공"}
        except Exception as e:
            return {"ok": False, "msg": f"로그인 실패: {e}"}

    def sign_out(self) -> dict:
        try:
            if self.auth_client:
                try:
                    self.auth_client.auth.sign_out()
                except Exception:
                    pass
            for k in ["sb_access_token", "sb_refresh_token", "sb_user_email", "sb_user_id"]:
                st.session_state.pop(k, None)
            return {"ok": True, "msg": "로그아웃 완료"}
        except Exception as e:
            return {"ok": False, "msg": f"로그아웃 실패: {e}"}

    def _get_db_client(self):
        if not self.is_active:
            return None
        if self.admin_client:
            return self.admin_client
        token = st.session_state.get("sb_access_token")
        if not token or not self.url or not self.anon_key:
            return None
        if ClientOptions is None:
            return self.auth_client
        try:
            opts = ClientOptions(headers={"Authorization": f"Bearer {token}", "apikey": self.anon_key})
            return create_client(self.url, self.anon_key, options=opts)
        except Exception:
            return self.auth_client

    # ---- law_reports ----
    def insert_initial_report(self, res: dict) -> dict:
        c = self._get_db_client()
        if not c:
            return {"ok": False, "msg": "DB 저장 불가(로그인 필요)", "id": None}
        try:
            followup = {"count": 0, "messages": [], "extra_context": ""}
            data = {
                "situation": res.get("situation", ""),
                "law_name": res.get("law_title", ""),
                "summary": {
                    "meta": res.get("meta"),
                    "case_plan": res.get("case_plan"),
                    "citations": res.get("citations"),
                    "law_text": res.get("law_text"),
                    "search_text": res.get("search_text"),
                    "doc": res.get("doc"),
                    "followup": followup,
                    "timings": res.get("timings"),
                },
                "user_email": st.session_state.get("sb_user_email"),
                "user_id": st.session_state.get("sb_user_id"),
            }
            resp = c.table("law_reports").insert(data).execute()
            d = getattr(resp, "data", None)
            inserted_id = d[0].get("id") if isinstance(d, list) and d else None
            return {"ok": True, "msg": "DB 저장 성공", "id": inserted_id}
        except Exception as e:
            return {"ok": False, "msg": f"DB 저장 실패: {e}", "id": None}

    def update_followup(self, report_id, res: dict, followup: dict) -> dict:
        c = self._get_db_client()
        if not c:
            return {"ok": False, "msg": "DB 업데이트 불가"}
        summary = {
            "meta": res.get("meta"),
            "case_plan": res.get("case_plan"),
            "citations": res.get("citations"),
            "law_text": res.get("law_text"),
            "search_text": res.get("search_text"),
            "doc": res.get("doc"),
            "followup": followup,
            "timings": res.get("timings"),
        }
        if report_id:
            try:
                c.table("law_reports").update({"summary": summary}).eq("id", report_id).execute()
                return {"ok": True, "msg": "DB 업데이트 성공"}
            except Exception:
                pass
        return {"ok": False, "msg": "DB 업데이트 실패"}

    def list_reports(self, limit: int = 50, keyword: str = "") -> list:
        c = self._get_db_client()
        if not c:
            return []
        try:
            q = c.table("law_reports").select("id, created_at, situation, law_name").order("created_at", desc=True).limit(limit)
            if keyword:
                q = q.ilike("situation", f"%{keyword}%")
            resp = q.execute()
            return getattr(resp, "data", None) or []
        except Exception:
            return []

    def get_report(self, report_id: str) -> Optional[dict]:
        c = self._get_db_client()
        if not c:
            return None
        try:
            resp = c.table("law_reports").select("*").eq("id", report_id).limit(1).execute()
            d = getattr(resp, "data", None)
            return d[0] if isinstance(d, list) and d else None
        except Exception:
            return None

    def delete_report(self, report_id: str) -> dict:
        c = self._get_db_client()
        if not c:
            return {"ok": False, "msg": "권한 없음"}
        try:
            c.table("law_reports").delete().eq("id", report_id).execute()
            return {"ok": True, "msg": "삭제 완료"}
        except Exception as e:
            return {"ok": False, "msg": f"삭제 실패: {e}"}


class LawOfficialService:
    """국가법령정보센터 API → Citation 중심"""

    def __init__(self):
        self.api_id = _safe_secrets("general").get("LAW_API_ID")

    @staticmethod
    def detect_doc_type(name: str) -> str:
        admrul_keywords = ["훈령", "예규", "고시", "지침", "요령", "규정", "기준", "지시", "공고"]
        name_lower = (name or "").lower()
        for kw in admrul_keywords:
            if kw in name_lower:
                return "admrul"
        return "law"

    def _law_html_link(self, mst_id: str) -> Optional[str]:
        if not self.api_id or not mst_id:
            return None
        return f"https://www.law.go.kr/DRF/lawService.do?OC={self.api_id}&target=law&MST={mst_id}&type=HTML"

    def _admrul_html_link(self, admrul_id: str) -> Optional[str]:
        if not self.api_id or not admrul_id:
            return None
        return f"https://www.law.go.kr/DRF/lawService.do?OC={self.api_id}&target=admrul&ID={admrul_id}&type=HTML"

    def get_law_excerpt(self, law_name: str, article_num: Optional[int] = None) -> Tuple[str, Optional[dict]]:
        """
        반환:
          - excerpt 텍스트(짧게)
          - citation 객체 (LAW)
        """
        if not self.api_id:
            return "⚠️ LAW_API_ID 미설정", None

        try:
            mst_id = cached_law_search(self.api_id, law_name) or ""
            if not mst_id:
                return f"🔍 '{law_name}' 검색 결과 없음", None
        except Exception as e:
            return f"API 검색 오류: {e}", None

        link = self._law_html_link(mst_id)
        try:
            xml_text = cached_law_detail_xml(self.api_id, mst_id)
            root = _safe_et_from_bytes(xml_text.encode("utf-8", errors="ignore"))

            # 조문 찾기
            if article_num:
                target = str(article_num)
                for art in root.findall(".//조문단위"):
                    jo_num = art.find("조문번호")
                    jo_content = art.find("조문내용")
                    if jo_num is None or jo_content is None:
                        continue
                    num_txt = (jo_num.text or "").strip()
                    if num_txt == target or num_txt.startswith(target):
                        body = (jo_content.text or "").strip()
                        body = re.sub(r"\s+", " ", body)[:550]
                        cite = {
                            "title": f"{law_name} 제{num_txt}조",
                            "type": "LAW",
                            "url": link or "",
                            "note": "조문 발췌(요약)",
                            "confidence": 0.95
                        }
                        return f"[{law_name} 제{num_txt}조] {body}", cite

            # 조문번호 없거나 매칭 실패 → 법령 존재만
            cite = {"title": law_name, "type": "LAW", "url": link or "", "note": "법령 원문 링크", "confidence": 0.85}
            return f"✅ '{law_name}' 확인됨 (조문 자동매칭 실패)", cite

        except Exception as e:
            cite = {"title": law_name, "type": "LAW", "url": link or "", "note": "법령 원문 링크", "confidence": 0.6}
            return f"법령 파싱 실패: {e}", cite

    def get_admrul_excerpt(self, name: str) -> Tuple[str, Optional[dict]]:
        if not self.api_id:
            return "⚠️ LAW_API_ID 미설정", None

        try:
            admrul_id = cached_admrul_search(self.api_id, name) or ""
            if not admrul_id:
                return f"🔍 '{name}' 행정규칙 검색 결과 없음", None
        except Exception as e:
            return f"행정규칙 검색 오류: {e}", None

        link = self._admrul_html_link(admrul_id)
        try:
            xml_text = cached_admrul_detail(self.api_id, admrul_id)
            root = _safe_et_from_bytes(xml_text.encode("utf-8", errors="ignore"))

            title = (root.findtext(".//행정규칙명") or root.findtext(".//admrulNm") or name).strip()
            content = (root.findtext(".//본문") or root.findtext(".//content") or "").strip()
            content = re.sub(r"\s+", " ", content)[:550] if content else ""

            cite = {"title": title, "type": "ADMRUL", "url": link or "", "note": "행정규칙 원문 링크", "confidence": 0.9}
            if content:
                return f"[{title}] {content}", cite
            return f"✅ '{title}' 확인됨 (본문 자동추출 실패)", cite
        except Exception as e:
            cite = {"title": name, "type": "ADMRUL", "url": link or "", "note": "행정규칙 원문 링크", "confidence": 0.6}
            return f"행정규칙 파싱 실패: {e}", cite

    def ai_search_text(self, query: str, top_k: int = 5) -> str:
        if not self.api_id:
            return "⚠️ LAW_API_ID 미설정"
        results = cached_ai_search(self.api_id, query, top_k)
        if not results:
            return f"🔍 '{query}' 지능형 검색 결과 없음"
        lines = [f"🔎 **지능형 검색 결과 ('{query}')**", "---"]
        for i, r in enumerate(results[:top_k], 1):
            title = r.get("title", "")
            link = r.get("link", "")
            doc_type = r.get("type", "")
            if link:
                lines.append(f"{i}. [{title}]({link}) ({doc_type})")
            else:
                lines.append(f"{i}. {title} ({doc_type})")
        return "\n".join(lines)


class FormService:
    """
    좌표 기반 서식 템플릿(JSON) + PDF 오버레이 생성
    - 템플릿은 SessionState에 저장(기본)
    - (선택) DB에 저장하고 싶으면 테이블 생성 후 연결해서 확장
    """

    @staticmethod
    def mm_to_pt(v_mm: float) -> float:
        if mm is None:
            # reportlab 없으면 대략 환산
            return float(v_mm) * 2.83464567
        return float(v_mm) * mm

    @staticmethod
    def pt_to_mm(v_pt: float) -> float:
        return float(v_pt) / 2.83464567

    def normalize_template(self, tpl: dict) -> dict:
        # 최소 방어
        tpl = tpl or {}
        tpl.setdefault("template_id", "template_" + str(int(time.time())))
        tpl.setdefault("name", "서식 템플릿")
        tpl.setdefault("page_size", "A4")
        tpl.setdefault("unit", "mm")
        tpl.setdefault("fields", [])
        if not isinstance(tpl["fields"], list):
            tpl["fields"] = []
        # 필드 기본값
        for f in tpl["fields"]:
            f.setdefault("page", 1)
            f.setdefault("font", "Helvetica")
            f.setdefault("size", 11)
        return tpl

    def ensure_state(self):
        st.session_state.setdefault("form_templates", {})
        st.session_state.setdefault("form_template_pdf_bytes", {})  # template_id -> pdf bytes

    def save_template(self, tpl: dict, pdf_bytes: Optional[bytes] = None):
        self.ensure_state()
        tpl = self.normalize_template(tpl)
        st.session_state["form_templates"][tpl["template_id"]] = tpl
        if pdf_bytes:
            st.session_state["form_template_pdf_bytes"][tpl["template_id"]] = pdf_bytes

    def list_templates(self) -> List[dict]:
        self.ensure_state()
        return list(st.session_state["form_templates"].values())

    def get_template(self, template_id: str) -> Optional[dict]:
        self.ensure_state()
        return st.session_state["form_templates"].get(template_id)

    def get_template_pdf(self, template_id: str) -> Optional[bytes]:
        self.ensure_state()
        return st.session_state["form_template_pdf_bytes"].get(template_id)

    def make_overlay_pdf(self, tpl: dict, values: dict) -> Optional[bytes]:
        if rl_canvas is None or io is None:
            return None
        tpl = self.normalize_template(tpl)
        unit = (tpl.get("unit") or "mm").lower()

        # A4 기본. (필요하면 확장)
        page_w, page_h = (595.2756, 841.8898)  # A4 pt

        buff = io.BytesIO()
        c = rl_canvas.Canvas(buff, pagesize=(page_w, page_h))

        # 페이지별로 텍스트 찍기
        max_page = 1
        for f in tpl["fields"]:
            max_page = max(max_page, _safe_int(f.get("page", 1), 1))

        for p in range(1, max_page + 1):
            for f in tpl["fields"]:
                if _safe_int(f.get("page", 1), 1) != p:
                    continue
                key = f.get("key", "")
                text = str(values.get(key, "") or "")
                if not text:
                    continue

                x = float(f.get("x", 0))
                y = float(f.get("y", 0))
                w = float(f.get("w", 0))
                h = float(f.get("h", 0))
                font = f.get("font", "Helvetica")
                size = float(f.get("size", 11))

                # 단위 변환 (좌표 기준: "좌상단 기준"이 아니라, PDF는 좌하단 기준)
                # 템플릿은 실무 편의상 "상단에서 y 내려오는 mm"로 쓰는 게 편함.
                # 따라서 여기서는: y를 "상단 기준(mm)"로 받는다고 가정하고 변환.
                if unit == "mm":
                    x_pt = self.mm_to_pt(x)
                    y_from_top_pt = self.mm_to_pt(y)
                    w_pt = self.mm_to_pt(w)
                    h_pt = self.mm_to_pt(h)
                else:
                    x_pt = x
                    y_from_top_pt = y
                    w_pt = w
                    h_pt = h

                # 상단 기준 y -> PDF 좌하단 기준 y
                y_pt = page_h - y_from_top_pt - h_pt

                # 텍스트 찍기(단순). 필요하면 줄바꿈/자동축소 확장 가능.
                try:
                    c.setFont(font, size)
                except Exception:
                    c.setFont("Helvetica", size)

                # 박스 안 여백
                pad = 2
                c.drawString(x_pt + pad, y_pt + (h_pt * 0.25), text[:200])

            c.showPage()

        c.save()
        return buff.getvalue()

    def merge_with_template_pdf(self, template_pdf: bytes, overlay_pdf: bytes) -> Optional[bytes]:
        if PdfReader is None or PdfWriter is None or io is None:
            return None
        try:
            base_reader = PdfReader(io.BytesIO(template_pdf))
            over_reader = PdfReader(io.BytesIO(overlay_pdf))
            writer = PdfWriter()

            n = max(len(base_reader.pages), len(over_reader.pages))
            for i in range(n):
                if i < len(base_reader.pages):
                    page = base_reader.pages[i]
                else:
                    # base page 없으면 overlay page만
                    page = over_reader.pages[i]
                    writer.add_page(page)
                    continue

                if i < len(over_reader.pages):
                    page.merge_page(over_reader.pages[i])

                writer.add_page(page)

            out = io.BytesIO()
            writer.write(out)
            return out.getvalue()
        except Exception:
            return None

    def generate_filled_pdf(self, template_id: str, values: dict) -> Tuple[Optional[bytes], str]:
        tpl = self.get_template(template_id)
        if not tpl:
            return None, "템플릿이 없습니다."

        overlay = self.make_overlay_pdf(tpl, values)
        if overlay is None:
            return None, "PDF 생성 모듈(reportlab) 미설치"

        template_pdf = self.get_template_pdf(template_id)
        if template_pdf:
            merged = self.merge_with_template_pdf(template_pdf, overlay)
            if merged:
                return merged, "OK(템플릿+오버레이)"
            # 병합 실패 시 overlay만 반환
            return overlay, "OK(오버레이만 - 병합모듈(pypdf) 확인)"
        else:
            return overlay, "OK(오버레이만 - 원본 PDF 미첨부)"


# ==========================================
# 6) Global Instances
# ==========================================
_SERVICE_VERSION = "v7_citations_forms"

@st.cache_resource(show_spinner=False)
def _get_services(_version: str = _SERVICE_VERSION):
    return LLMService(), SearchService(), DatabaseService(), LawOfficialService(), FormService()

llm_service, search_service, db_service, law_api_service, form_service = _get_services()


# ==========================================
# 7) Agents (Research -> Citations, Plan -> JSON, Draft -> JSON)
# ==========================================
class LegalAgents:
    @staticmethod
    def researcher(situation: str) -> dict:
        """
        1) LLM이 '찾아야 할 문서(법령/행정규칙)' 후보를 JSON으로 뽑음
        2) 국가법령정보센터 API로 실제 링크/발췌를 가져와 citations 생성
        반환:
          {
            "law_text": "...",
            "citations": [...],
          }
        """
        prompt_extract = f"""상황: "{situation}"
위 민원 처리를 위해 "반드시 확인할" 대한민국 근거 문서를 최대 4개 JSON 리스트로 추출.
- doc_type: "law" 또는 "admrul"
- article_num: 조문번호(모르면 null)
출력 예시:
[
  {{"name":"자동차관리법","article_num":26,"doc_type":"law"}},
  {{"name":"무단방치자동차 처리지침","article_num":null,"doc_type":"admrul"}}
]
규칙: JSON만."""
        targets = []
        extracted = llm_service.generate_json(prompt_extract)
        if isinstance(extracted, list):
            targets = extracted
        elif isinstance(extracted, dict):
            targets = [extracted]
        if not targets:
            targets = [{"name": "관련 법령", "article_num": None, "doc_type": "law"}]

        citations: List[dict] = []
        lines: List[str] = [f"🔍 **근거 문서 조회 결과 ({len(targets)}건)**", "---"]

        def fetch_one(item: Dict[str, Any]) -> Tuple[str, Optional[dict]]:
            name = str(item.get("name") or "").strip() or "관련 법령"
            doc_type = str(item.get("doc_type") or law_api_service.detect_doc_type(name)).lower()
            article_num = item.get("article_num", None)
            art = None
            if article_num is not None:
                art = _safe_int(article_num, 0) or None

            if doc_type == "admrul":
                return law_api_service.get_admrul_excerpt(name)
            return law_api_service.get_law_excerpt(name, art)

        results: List[Tuple[str, Optional[dict]]] = []
        try:
            with ThreadPoolExecutor(max_workers=min(LAW_MAX_WORKERS, len(targets))) as ex:
                futures = [ex.submit(fetch_one, it) for it in targets]
                for f in as_completed(futures):
                    results.append(f.result())
        except Exception:
            results = [fetch_one(it) for it in targets]

        ok_cnt = 0
        for excerpt, cite in results:
            if cite and cite.get("url"):
                citations.append(cite)
                ok_cnt += 1
                lines.append(f"✅ **[{cite['title']}]({cite['url']})**\n{excerpt}\n")
            else:
                # url 없더라도 텍스트만
                lines.append(f"⚠️ {excerpt}")

        # API가 다 죽으면 경고문 리턴
        if ok_cnt == 0:
            warn = (
                "⚠️ **[API 조회 실패: AI 추론 금지 모드]**\n"
                "- 현재 법령 API 설정/응답이 없습니다.\n"
                "- 이 상태에서는 근거 링크를 제공할 수 없으니, LAW_API_ID / 네트워크를 확인하세요."
            )
            return {"law_text": warn, "citations": []}

        return {"law_text": "\n".join(lines), "citations": citations}

    @staticmethod
    def case_planner(situation: str, citations: List[dict], law_text: str, search_text: str, news_cites: List[dict]) -> dict:
        """
        “9급도 따라가는” 처리 구조를 JSON으로 강제
        - flow_steps / key_points / risks
        - citations: 반드시 url 포함
        """
        # citations 합치기(법령/행정규칙 + 뉴스)
        merged_cites = (citations or []) + (news_cites or [])
        # 프롬프트 내 citations는 "요약 리스트"로만 제공
        cite_txt = "\n".join([f"- {c.get('type','')} | {c.get('title','')} | {c.get('url','')}" for c in merged_cites])[:1500]

        prompt = f"""당신은 '텐베거 행정가' + '법률검토 담당' 역할입니다.
아래 민원에 대해, 9급도 그대로 따라할 수 있는 처리 계획을 JSON으로 작성하세요.

[민원]
{situation}

[근거 링크 후보(반드시 이 중에서 citations 구성)]
{cite_txt}

[법령 발췌(참고)]
{_strip_html(law_text)[:1200]}

[뉴스/사례(참고)]
{_strip_html(search_text)[:800]}

[출력 JSON 스키마]
- case_type: 업무유형 한 줄(예: 무단방치차량 처리 / 건설기계 번호판 관련 등)
- flow_steps: 순서형 단계(최소 5개). 각 단계는 '무엇을/어디에/무슨 산출물'까지 포함.
- key_points: 핵심 5개(감사/민원에서 중요한 포인트)
- risks: 주의 5개(절차 하자/사실확인/기한/통지/증거 등)
- citations: 반드시 url 포함. title/type/url/note/confidence

규칙:
1) 서론/인사말 금지
2) citations는 위 후보 링크를 우선 사용. 모르면 type="ETC", url="".

JSON만 출력."""
        plan = llm_service.generate_json(prompt, schema=CASE_PLAN_SCHEMA)
        if not isinstance(plan, dict):
            # 최소 fallback
            plan = {
                "case_type": "민원 처리(분류 실패)",
                "flow_steps": [
                    "1) 민원 요지/요구사항을 1문장으로 확정하고 사실관계를 분리 기록",
                    "2) 관할/권한/처리기한(법정기한) 확인 후 내부 배당",
                    "3) 현장/자료 확인 → 증거(사진/대장/시스템 조회) 확보",
                    "4) 적용 근거(법령/행정규칙) 확인 후 처분/안내 여부 판단",
                    "5) 통지문 작성(근거조문 명시) → 발송/수령 증빙 확보",
                    "6) 이의신청/행정심판/소송 안내 문구 포함 후 종결"
                ],
                "key_points": [
                    "사실확인(객관증거) 없이 판단 금지",
                    "관할/권한/처리기한 우선 확정",
                    "통지/송달 증빙 확보",
                    "근거 링크(원문) 반드시 첨부",
                    "개인정보 마스킹"
                ],
                "risks": [
                    "절차 하자(사전통지/의견제출) 누락",
                    "기한 산정 오류",
                    "근거 부정확(조문/규정 혼동)",
                    "사실관계 오인",
                    "개인정보 노출"
                ],
                "citations": merged_cites[:6] if merged_cites else []
            }
        # citations 비었으면 최소 넣기
        plan.setdefault("citations", merged_cites[:6] if merged_cites else [])
        return plan

    @staticmethod
    def clerk_meta(situation: str) -> dict:
        today = _now_kst()
        doc_num = f"행정-{today.strftime('%Y')}-{int(time.time())%1000:03d}호"
        return {
            "today_str": today.strftime("%Y. %m. %d."),
            "doc_num": doc_num
        }

    @staticmethod
    def drafter(situation: str, plan: dict, meta: dict) -> dict:
        """
        공문서는 '계획(Flow)' 기반으로 생성.
        - 근거는 citations에서 title을 인용 문구로 반영
        """
        # citations 텍스트
        cits = plan.get("citations", []) if isinstance(plan, dict) else []
        cit_titles = [c.get("title", "") for c in cits if isinstance(c, dict)]
        cit_line = ", ".join([t for t in cit_titles if t])[:180]

        prompt = f"""너는 20년 경력 행정 서기.
아래 정보로 '완결된 공문서'를 JSON으로 작성.

[민원]
{situation}

[처리 계획]
- case_type: {plan.get('case_type','')}
- flow_steps:
{chr(10).join([f"- {x}" for x in (plan.get('flow_steps') or [])])}

[핵심/주의]
- key: {", ".join((plan.get("key_points") or [])[:5])}
- risks: {", ".join((plan.get("risks") or [])[:5])}

[근거(제목)]
{cit_line}

[문서 메타]
- 문서번호: {meta.get("doc_num","")}
- 시행일: {meta.get("today_str","")}

[작성 원칙]
1) 서론/인사말 금지. 바로 본문.
2) 구조: (1)경위 (2)사실확인 (3)근거 (4)처리/안내 (5)이의제기
3) 개인정보는 OOO/○○○로 마스킹
4) 근거는 "「...」" 형태로 2개 이상 포함(가능하면)
5) 이의제기: 행정심판(90일), 행정소송(1년) 문구 포함

JSON만 출력."""
        doc = llm_service.generate_json(prompt, schema=DOC_SCHEMA)
        if not isinstance(doc, dict):
            doc = {
                "title": f"{plan.get('case_type','민원')} 처리 안내",
                "receiver": "민원인 OOO 귀하",
                "body_paragraphs": [
                    "1. (경위) 귀하께서 신고하신 사안과 관련하여 처리 내용을 안내드립니다.",
                    "2. (사실확인) 관할 부서에서 관련 자료 및 현장 확인을 실시하였음.",
                    "3. (근거) 관련 법령 및 행정규칙에 따라 필요한 조치를 검토·시행하였음.",
                    "4. (처리/안내) 확인 결과 및 조치 내용은 다음과 같음. (세부 내용 별도 기재)",
                    "5. (이의제기) 본 처분/안내에 이의가 있는 경우 「행정심판법」에 따라 90일 이내 행정심판, 「행정소송법」에 따라 1년 이내 행정소송을 제기할 수 있음."
                ],
                "department_head": "OO시 OO과장"
            }

        # 방어
        bp = doc.get("body_paragraphs", [])
        doc["body_paragraphs"] = [bp] if isinstance(bp, str) else (bp if isinstance(bp, list) else [])
        for k in ["title", "receiver", "department_head"]:
            if not isinstance(doc.get(k), str):
                doc[k] = ""
        return doc


# ==========================================
# 8) Workflow
# ==========================================
def run_workflow(user_input: str) -> dict:
    log_placeholder = st.empty()
    logs: List[str] = []
    timings: Dict[str, float] = {}

    def add_log(msg: str, style: str = "sys"):
        logs.append(f"<div class='agent-log log-{style}'>{_escape(msg)}</div>")
        log_placeholder.markdown("".join(logs), unsafe_allow_html=True)

    t0 = time.perf_counter()

    add_log("🔍 Phase 1: 근거 문서(법령/행정규칙) 조회...", "legal")
    t = time.perf_counter()
    law_pack = LegalAgents.researcher(user_input)
    law_text = law_pack.get("law_text", "")
    law_cites = law_pack.get("citations", []) or []
    timings["law_sec"] = round(time.perf_counter() - t, 2)
    add_log(f"📜 근거 조회 완료 ({timings['law_sec']}s)", "legal")

    add_log("🟦 Phase 2: 뉴스/사례(가능 시) 조회...", "search")
    t = time.perf_counter()
    try:
        search_text, news_cites = search_service.search_precedents(user_input)
    except Exception:
        search_text, news_cites = "검색 모듈 미연결", []
    timings["news_sec"] = round(time.perf_counter() - t, 2)
    add_log(f"📰 사례 조회 완료 ({timings['news_sec']}s)", "search")

    add_log("🧠 Phase 3: 처리 계획(Flow/Key/Risk) 구조화...", "strat")
    t = time.perf_counter()
    case_plan = LegalAgents.case_planner(user_input, law_cites, law_text, search_text, news_cites)
    timings["plan_sec"] = round(time.perf_counter() - t, 2)

    add_log("📅 Phase 4: 메타 생성...", "calc")
    t = time.perf_counter()
    meta_info = LegalAgents.clerk_meta(user_input)
    timings["meta_sec"] = round(time.perf_counter() - t, 2)

    add_log("✍️ Phase 5: 공문서 생성(JSON)...", "draft")
    t = time.perf_counter()
    doc_data = LegalAgents.drafter(user_input, case_plan, meta_info)
    timings["draft_sec"] = round(time.perf_counter() - t, 2)

    timings["total_sec"] = round(time.perf_counter() - t0, 2)
    log_placeholder.empty()

    citations_all = (case_plan.get("citations") or []) if isinstance(case_plan, dict) else []

    return {
        "situation": user_input,
        "law_title": (citations_all[0].get("title") if citations_all else ""),
        "law_text": law_text,
        "search_text": search_text,
        "citations": citations_all,
        "case_plan": case_plan,
        "doc": doc_data,
        "meta": meta_info,
        "timings": timings,
    }


# ==========================================
# 9) Follow-up Chat (근거 링크 추가조회 가능)
# ==========================================
def build_case_context(res: dict) -> str:
    situation = res.get("situation", "")
    plan = res.get("case_plan") or {}
    doc = res.get("doc") or {}
    cites = res.get("citations") or []

    cite_txt = "\n".join([f"- {c.get('type','')} | {c.get('title','')} | {c.get('url','')}" for c in cites])[:1200]

    return f"""[케이스 컨텍스트]
1) 민원: {situation}

2) 처리계획:
- case_type: {plan.get('case_type','')}
- flow:
{chr(10).join([f"- {x}" for x in (plan.get('flow_steps') or [])])}

- key:
{chr(10).join([f"- {x}" for x in (plan.get('key_points') or [])])}

- risks:
{chr(10).join([f"- {x}" for x in (plan.get('risks') or [])])}

3) 근거 링크:
{cite_txt}

4) 공문:
- 제목: {doc.get('title','')}
- 수신: {doc.get('receiver','')}

[규칙]
- 컨텍스트 밖 단정 금지
- 근거가 없으면 '추가 확인 필요'라고 말할 것
- 서론/인사말 금지. 바로 답."""
    

def answer_followup(case_ctx: str, extra_ctx: str, history: list, user_msg: str) -> str:
    hist = history[-8:]
    hist_txt = "\n".join([f"{m['role']}: {m['content']}" for m in hist]) if hist else ""
    prompt = f"""{case_ctx}
[추가 조회] {extra_ctx or '없음'}
[히스토리] {hist_txt}
[질문] {user_msg}

규칙:
- 가능한 경우: 근거 링크(있으면) 함께 안내
- 부족하면: 무엇이 부족한지 구체적으로 말하고 '추가 조회 필요' 명시
- 서론 금지."""
    return llm_service.generate_text(prompt)


def render_followup_chat(res: dict):
    st.session_state.setdefault("followup_count", 0)
    st.session_state.setdefault("followup_messages", [])
    st.session_state.setdefault("followup_extra_context", "")

    remain = max(0, MAX_FOLLOWUP_Q - st.session_state["followup_count"])
    st.info(f"후속 질문: **{remain}/{MAX_FOLLOWUP_Q}**")

    if remain == 0:
        st.warning("후속 질문 한도(5회) 소진")
        return

    for m in st.session_state["followup_messages"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    user_q = st.chat_input("후속 질문 (최대 5회)")
    if not user_q:
        return

    st.session_state["followup_messages"].append({"role": "user", "content": user_q})
    st.session_state["followup_count"] += 1

    with st.chat_message("user"):
        st.markdown(user_q)

    # 추가 근거 조회(선택): 사용자가 “법령/원문/조문/지침” 등을 물으면 AIS 검색도 제공
    extra_ctx = st.session_state.get("followup_extra_context", "")
    q_low = (user_q or "").lower()
    if any(k in q_low for k in ["조문", "법령", "원문", "지침", "예규", "훈령", "고시"]):
        # 간단히 AIS 검색 결과 붙이기(설정된 경우)
        try:
            extra_ctx += "\n\n" + law_api_service.ai_search_text(user_q[:60], top_k=5)
        except Exception:
            pass
        st.session_state["followup_extra_context"] = extra_ctx

    case_ctx = build_case_context(res)

    with st.chat_message("assistant"):
        with st.spinner("답변 생성..."):
            ans = answer_followup(case_ctx, st.session_state.get("followup_extra_context", ""),
                                  st.session_state["followup_messages"], user_q)
            st.markdown(ans)

    st.session_state["followup_messages"].append({"role": "assistant", "content": ans})

    # DB 업데이트
    followup_data = {
        "count": st.session_state["followup_count"],
        "messages": st.session_state["followup_messages"],
        "extra_context": st.session_state.get("followup_extra_context", "")
    }
    upd = db_service.update_followup(st.session_state.get("report_id"), res, followup_data)
    if not upd.get("ok"):
        st.caption(f"⚠️ {upd.get('msg')}")


# ==========================================
# 10) Login & Data Management UI
# ==========================================
def render_login_box():
    with st.expander("🔐 로그인 (Supabase Auth)", expanded=not db_service.is_logged_in()):
        if not db_service.is_active:
            st.error("Supabase 연결 실패. secrets 확인 필요.")
            return

        if db_service.is_logged_in():
            st.success(f"✅ {st.session_state.get('sb_user_email')}")
            if st.button("로그아웃", use_container_width=True):
                out = db_service.sign_out()
                if out.get("ok"):
                    st.rerun()
                else:
                    st.error(out.get("msg"))
        else:
            email = st.text_input("이메일", key="login_email")
            if email and not email.lower().endswith(KOREA_DOMAIN):
                st.warning(f"⚠️ {KOREA_DOMAIN} 계정 권장 (권한정책은 RLS로 제어 권장)")
            pw = st.text_input("비밀번호", type="password", key="login_pw")
            if st.button("로그인", type="primary", use_container_width=True):
                r = db_service.sign_in(email, pw)
                if r.get("ok"):
                    st.rerun()
                else:
                    st.error(r.get("msg"))


def render_data_management_panel():
    with st.expander("🗂️ 히스토리/데이터 관리", expanded=False):
        if not db_service.is_logged_in() and not db_service.service_key:
            st.info("로그인 후 사용 가능")
            return

        if db_service.service_key:
            st.caption("⚠️ 관리자 모드 (SERVICE_ROLE_KEY)")

        col1, col2 = st.columns([1, 1])
        with col1:
            keyword = st.text_input("검색", placeholder="키워드")
        with col2:
            limit = st.slider("개수", 10, 100, 30, 10)

        rows = db_service.list_reports(limit=limit, keyword=keyword)
        if not rows:
            st.caption("결과 없음")
            return

        options = []
        id_map = {}
        for r in rows:
            rid = r.get("id")
            created = (r.get("created_at") or "")[:16].replace("T", " ")
            sit = (r.get("situation") or "").replace("\n", " ")[:40]
            label = f"{created} | {sit}"
            options.append(label)
            id_map[label] = rid

        picked = st.selectbox("선택", options)
        report_id = id_map.get(picked)
        detail = db_service.get_report(report_id) if report_id else None
        if not detail:
            return

        st.json(detail)

        c1, c2 = st.columns(2)
        with c1:
            st.download_button("⬇️ JSON", json.dumps(detail, ensure_ascii=False, indent=2).encode(),
                               f"report_{report_id}.json", "application/json", use_container_width=True)
        with c2:
            if st.button("🗑️ 삭제", use_container_width=True):
                r = db_service.delete_report(report_id)
                st.success("삭제됨") if r.get("ok") else st.error(r.get("msg"))
                if r.get("ok"):
                    st.rerun()


# ==========================================
# 11) Form UI (좌표 템플릿 + PDF 오버레이)
# ==========================================
def render_form_builder():
    form_service.ensure_state()

    with st.expander("🧩 좌표 기반 서식 템플릿(추출X) / PDF 오버레이", expanded=False):
        st.caption("원본 서식은 그대로 두고, 빈칸 좌표만 저장해 텍스트를 찍어 PDF로 생성합니다.")

        cols = st.columns([1, 1])
        with cols[0]:
            st.markdown("#### 1) 템플릿 생성/편집(JSON)")
            default_tpl = {
                "template_id": "template_" + str(int(time.time())),
                "name": "서식 템플릿",
                "page_size": "A4",
                "unit": "mm",
                "fields": [
                    {"key": "receiver", "label": "수신", "page": 1, "x": 30, "y": 40, "w": 120, "h": 8, "font": "Helvetica", "size": 11},
                    {"key": "doc_num", "label": "문서번호", "page": 1, "x": 30, "y": 55, "w": 120, "h": 8, "font": "Helvetica", "size": 10},
                    {"key": "today_str", "label": "시행일", "page": 1, "x": 30, "y": 63, "w": 120, "h": 8, "font": "Helvetica", "size": 10},
                ],
            }
            tpl_json = st.text_area("템플릿 JSON", height=240, value=json.dumps(default_tpl, ensure_ascii=False, indent=2))
            pdf_file = st.file_uploader("원본 서식 PDF(선택)", type=["pdf"], help="첨부하면 오버레이와 병합해 완성 PDF를 만듭니다.")
            if st.button("💾 템플릿 저장", use_container_width=True):
                try:
                    tpl = json.loads(tpl_json)
                    pdf_bytes = pdf_file.read() if pdf_file else None
                    form_service.save_template(tpl, pdf_bytes=pdf_bytes)
                    st.success("저장 완료")
                except Exception as e:
                    st.error(f"저장 실패: {e}")

        with cols[1]:
            st.markdown("#### 2) 템플릿 선택 → 값 입력 → PDF 생성")
            templates = form_service.list_templates()
            if not templates:
                st.info("저장된 템플릿이 없습니다.")
                return

            opt = {f"{t['template_id']} | {t.get('name','')}" : t["template_id"] for t in templates}
            picked = st.selectbox("템플릿 선택", list(opt.keys()))
            tid = opt.get(picked)
            tpl = form_service.get_template(tid) if tid else None
            if not tpl:
                return

            # 자동 입력값 소스: (워크플로 결과가 있으면) meta/doc에서 채우기
            values = {}
            wf = st.session_state.get("workflow_result")
            if wf:
                meta = wf.get("meta", {}) or {}
                doc = wf.get("doc", {}) or {}
                values.update(meta)
                values.update(doc)
                # body는 키가 없으니 제외

            st.caption("아래 key=value를 JSON으로 입력(템플릿 fields의 key와 일치해야 함)")
            v_json = st.text_area("값 JSON", height=140, value=json.dumps(values, ensure_ascii=False, indent=2))

            c1, c2 = st.columns(2)
            with c1:
                st.download_button("⬇️ 템플릿 JSON", json.dumps(tpl, ensure_ascii=False, indent=2).encode(),
                                   f"{tpl['template_id']}.json", "application/json", use_container_width=True)
            with c2:
                if st.button("🧾 PDF 생성", use_container_width=True):
                    try:
                        v = json.loads(v_json) if v_json else {}
                        pdf_bytes, msg = form_service.generate_filled_pdf(tid, v)
                        if not pdf_bytes:
                            st.error(msg)
                        else:
                            st.success(msg)
                            st.download_button("⬇️ 생성된 PDF 다운로드", pdf_bytes,
                                               f"filled_{tid}.pdf", "application/pdf", use_container_width=True)
                    except Exception as e:
                        st.error(f"PDF 생성 실패: {e}")

        # 환경 안내
        st.markdown("---")
        st.caption("⚙️ PDF 생성/병합 필요 라이브러리")
        st.code(
            "reportlab (PDF 오버레이 생성)\n"
            "pypdf (원본 템플릿 PDF와 병합)\n\n"
            "requirements.txt 예:\n"
            "streamlit>=1.32\n"
            "requests>=2.31\n"
            "reportlab>=4.0\n"
            "pypdf>=4.0\n",
            language="text"
        )


# ==========================================
# 12) Main UI
# ==========================================
def render_citations_panel(citations: List[dict]):
    if not citations:
        st.caption("근거 링크가 없습니다(LAW_API_ID/뉴스키 설정 확인).")
        return
    # 클릭 가능한 링크(칩 스타일 흉내)
    for c in citations[:20]:
        title = c.get("title", "근거")
        url = c.get("url", "")
        ctype = c.get("type", "ETC")
        note = c.get("note", "")
        if url:
            st.markdown(f"- **[{ctype}] [{title}]({url})**  \n  {note}")
        else:
            st.markdown(f"- **[{ctype}] {title}**  \n  {note}")


def main():
    # 상단 상태
    g = _safe_secrets("general")
    v = _safe_secrets("vertex")
    s = _safe_secrets("supabase")
    status_items = []
    status_items.append("✅법령" if g.get("LAW_API_ID") else "❌법령")
    status_items.append("✅뉴스" if (g.get("NAVER_CLIENT_ID") and g.get("NAVER_CLIENT_SECRET")) else "❌뉴스")
    status_items.append("✅AI" if v.get("SERVICE_ACCOUNT_JSON") else "❌AI")
    status_items.append("✅DB" if (s.get("SUPABASE_URL") and (s.get("SUPABASE_ANON_KEY") or s.get("SUPABASE_KEY"))) else "❌DB")

    top_cols = st.columns([6, 2])
    with top_cols[0]:
        st.caption(" | ".join(status_items) + (" | ⚠️관리자" if db_service.service_key else ""))
    with top_cols[1]:
        st.caption("⚠️ 개인정보(성명·연락처·주소·차량번호 등) 입력 금지")

    # Sidebar
    with st.sidebar:
        st.markdown("### 🏢 AI 행정관 Pro (One-Stop)")
        st.caption("근거 클릭 → 흐름/주의 → 공문 → 좌표서식 PDF")
        st.markdown("---")
        render_login_box()
        st.markdown("---")
        render_data_management_panel()

    # Main split
    col_left, col_right = st.columns([1, 1.25])

    with col_left:
        st.markdown("### 🗣️ 업무 지시")
        user_input = st.text_area(
            "업무 내용",
            height=150,
            label_visibility="collapsed",
            placeholder="예시\n- 상황: (무슨 일 / 어디 / 언제)\n- 쟁점: (무엇이 문제)\n- 요청: (원하는 결과/공문 종류)"
        )

        if st.button("⚡ 스마트 분석", type="primary", use_container_width=True):
            if not user_input:
                st.warning("내용 입력 필요")
            else:
                try:
                    with st.spinner("AI 에이전트 처리 중..."):
                        res = run_workflow(user_input)
                        ins = db_service.insert_initial_report(res)
                        res["save_msg"] = ins.get("msg")
                        st.session_state["report_id"] = ins.get("id")
                        st.session_state["workflow_result"] = res
                except Exception as e:
                    st.error(f"오류: {e}")

        # 좌표 서식 빌더(항상 노출)
        render_form_builder()

        if "workflow_result" in st.session_state:
            res = st.session_state["workflow_result"]
            st.markdown("---")
            if "성공" in (res.get("save_msg") or ""):
                st.success(f"✅ {res['save_msg']}")
            else:
                st.info(f"ℹ️ {res.get('save_msg','')}")

            with st.expander("⏱️ 소요시간", expanded=False):
                st.json(res.get("timings", {}))

            with st.expander("🔗 근거(클릭 → 원문)", expanded=True):
                render_citations_panel(res.get("citations") or [])

            with st.expander("🧭 처리 흐름/핵심/주의 (9급용)", expanded=True):
                plan = res.get("case_plan") or {}
                st.markdown(f"**업무유형:** {plan.get('case_type','')}")
                st.markdown("**처리 흐름(Flow)**")
                for x in (plan.get("flow_steps") or []):
                    st.markdown(f"- {x}")
                st.markdown("**핵심(Key)**")
                for x in (plan.get("key_points") or []):
                    st.markdown(f"- {x}")
                st.markdown("**주의(Risk)**")
                for x in (plan.get("risks") or []):
                    st.markdown(f"- {x}")

            with st.expander("📜 법령/규칙 발췌(참고)", expanded=False):
                st.markdown(res.get("law_text", ""))

            with st.expander("📰 뉴스/사례(참고)", expanded=False):
                st.markdown(res.get("search_text", ""))

    with col_right:
        if "workflow_result" in st.session_state:
            res = st.session_state["workflow_result"]
            doc = res.get("doc") or {}
            meta = res.get("meta", {}) or {}

            if doc:
                bp = doc.get("body_paragraphs", [])
                if isinstance(bp, str):
                    bp = [bp]
                body_html = "".join([f"<p style='margin-bottom:12px'>{_escape(str(p))}</p>" for p in bp])

                html = f"""<div class="paper-sheet">
<div class="stamp">직인생략</div>
<div class="doc-header">{_escape(doc.get('title','공문서'))}</div>
<div class="doc-info">
<span>문서번호: {_escape(meta.get('doc_num',''))}</span>
<span>시행일: {_escape(meta.get('today_str',''))}</span>
<span>수신: {_escape(doc.get('receiver',''))}</span>
</div>
<hr style="border:1px solid rgba(17,24,39,0.7);margin-bottom:18px">
<div class="doc-body">{body_html}</div>
<div class="doc-footer">{_escape(doc.get('department_head',''))}</div>
</div>"""
                st.markdown(html, unsafe_allow_html=True)
                st.markdown("---")
                with st.expander("💬 후속 질문 (최대 5회)", expanded=True):
                    render_followup_chat(res)
            else:
                st.warning("공문 생성 실패 (JSON 파싱 오류)")
        else:
            st.markdown(
                """<div style='text-align:center;padding:80px;color:#9ca3af;background:white;border-radius:12px;border:1px dashed #d1d5db'>
<h3>📄 Document Preview</h3>
<p>왼쪽에서 업무 지시 후<br/>공문서가 여기에 표시됩니다</p>
</div>""",
                unsafe_allow_html=True
            )


if __name__ == "__main__":
    main()
# streamlit_app.py
# -*- coding: utf-8 -*-

import json
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import escape as _escape
from typing import Optional, Dict, Any, List, Tuple

import streamlit as st

# ---------------------------
# Optional deps (Streamlit Cloud에서 누락 시 앱 전체가 죽지 않도록)
# ---------------------------
try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    from groq import Groq
except Exception:  # pragma: no cover
    Groq = None

try:
    from supabase import create_client
except Exception:  # pragma: no cover
    create_client = None

try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleAuthRequest
except Exception:  # pragma: no cover
    service_account = None
    GoogleAuthRequest = None


# ==========================================
# 0) Settings
# ==========================================
MAX_FOLLOWUP_Q = 5     # 후속 질문 최대 5회
LAW_MAX_WORKERS = 3    # 법령 병렬 조회 워커 수(너무 높이면 실패율↑)
HTTP_RETRIES = 2       # 외부 API 재시도 횟수
HTTP_TIMEOUT = 10      # 외부 API 타임아웃(초)
KST = timezone(timedelta(hours=9))


# ==========================================
# 1) Configuration & Styles
# ==========================================
st.set_page_config(layout="wide", page_title="AI Bureau: The Legal Glass", page_icon="⚖️")

st.markdown(
    """
<style>
    .stApp { background-color: #f3f4f6; }

    .paper-sheet {
        background-color: white;
        width: 100%;
        max-width: 210mm;
        min-height: 297mm;
        padding: 25mm;
        margin: auto;
        box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        font-family: 'Batang', serif;
        color: #111;
        line-height: 1.6;
        position: relative;
    }

    .doc-header { text-align: center; font-size: 22pt; font-weight: 900; margin-bottom: 30px; letter-spacing: 2px; }
    .doc-info { display: flex; justify-content: space-between; font-size: 11pt; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 20px; gap:10px; flex-wrap:wrap; }
    .doc-body { font-size: 12pt; text-align: justify; white-space: pre-line; }
    .doc-footer { text-align: center; font-size: 20pt; font-weight: bold; margin-top: 80px; letter-spacing: 5px; }
    .stamp { position: absolute; bottom: 85px; right: 80px; border: 3px solid #cc0000; color: #cc0000; padding: 5px 10px; font-size: 14pt; font-weight: bold; transform: rotate(-15deg); opacity: 0.8; border-radius: 5px; }

    .agent-log { font-family: 'Consolas', monospace; font-size: 0.85rem; padding: 6px 12px; border-radius: 6px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
    .log-legal { background-color: #eff6ff; color: #1e40af; border-left: 4px solid #3b82f6; }
    .log-search { background-color: #fff7ed; color: #c2410c; border-left: 4px solid #f97316; }
    .log-strat { background-color: #f5f3ff; color: #6d28d9; border-left: 4px solid #8b5cf6; }
    .log-calc { background-color: #f0fdf4; color: #166534; border-left: 4px solid #22c55e; }
    .log-draft { background-color: #fef2f2; color: #991b1b; border-left: 4px solid #ef4444; }
    .log-sys { background-color: #f3f4f6; color: #4b5563; border-left: 4px solid #9ca3af; }

    /* Streamlit Cloud 상단 Fork/GitHub 숨김 (버전별로 다를 수 있음) */
    header [data-testid="stToolbar"] { display: none !important; }
    header [data-testid="stDecoration"] { display: none !important; }
    header { height: 0px !important; }
    footer { display: none !important; }
    div[data-testid="stStatusWidget"] { display: none !important; }
</style>
""",
    unsafe_allow_html=True,
)


# ==========================================
# 2) Utils (HTTP, Cache)
# ==========================================
def _require_requests():
    if requests is None:
        raise RuntimeError("requests 패키지가 설치되지 않았습니다. requirements.txt에 requests를 추가하세요.")


def http_get(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = HTTP_TIMEOUT,
    retries: int = HTTP_RETRIES,
):
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
                time.sleep(0.2 * (2**i))
    raise Exception(last_err)


def http_post(
    url: str,
    json_body: dict,
    headers: Optional[dict] = None,
    timeout: int = HTTP_TIMEOUT,
    retries: int = HTTP_RETRIES,
):
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
                time.sleep(0.2 * (2**i))
    raise Exception(last_err)


def _safe_et_from_bytes(b: bytes) -> ET.Element:
    """XML 파싱이 깨질 때를 대비한 안전 파서"""
    try:
        return ET.fromstring(b)
    except Exception:
        try:
            return ET.fromstring(b.decode("utf-8", errors="ignore").encode("utf-8"))
        except Exception as e:
            raise e


@st.cache_data(ttl=86400, show_spinner=False)
def cached_law_search(api_id: str, law_name: str) -> str:
    """lawSearch.do -> MST(법령일련번호) 캐시"""
    base_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {"OC": api_id, "target": "law", "type": "XML", "query": law_name, "display": 1}
    r = http_get(base_url, params=params, timeout=8)
    root = _safe_et_from_bytes(r.content)
    law_node = root.find(".//law")
    if law_node is None:
        return ""
    return (law_node.findtext("법령일련번호") or "").strip()


@st.cache_data(ttl=86400, show_spinner=False)
def cached_law_detail_xml(api_id: str, mst_id: str) -> str:
    """lawService.do -> XML 전문 캐시"""
    service_url = "https://www.law.go.kr/DRF/lawService.do"
    params = {"OC": api_id, "target": "law", "type": "XML", "MST": mst_id}
    r = http_get(service_url, params=params, timeout=12)
    return r.text


@st.cache_data(ttl=600, show_spinner=False)
def cached_naver_news(query: str, top_k: int = 3) -> str:
    """네이버 뉴스 검색 결과 캐시(10분)"""
    g = st.secrets.get("general", {})
    client_id = g.get("NAVER_CLIENT_ID")
    client_secret = g.get("NAVER_CLIENT_SECRET")
    news_url = "https://openapi.naver.com/v1/search/news.json"

    if not client_id or not client_secret:
        return "⚠️ 네이버 API 키가 없습니다. (secrets.toml: [general] NAVER_CLIENT_ID/SECRET)"
    if not query:
        return "⚠️ 검색어가 비었습니다."

    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    params = {"query": query, "display": 10, "sort": "sim"}

    r = http_get(news_url, params=params, headers=headers, timeout=8)
    items = r.json().get("items", []) or []
    if not items:
        return f"🔍 `{query}` 관련 최신 사례가 없습니다."

    def clean_html(s: str) -> str:
        if not s:
            return ""
        s = re.sub(r"<[^>]+>", "", s)
        s = s.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        return s.strip()

    lines = [f"📰 **최신 뉴스 사례 (검색어: {query})**", "---"]
    for it in items[:top_k]:
        title = clean_html(it.get("title", ""))
        desc = clean_html(it.get("description", ""))
        link = it.get("link", "#")
        lines.append(f"- **[{title}]({link})**\n  : {desc[:150]}...")
    return "\n".join(lines)


# ==========================================
# 3) Infrastructure Services
# ==========================================
def _vertex_schema_from_doc_schema(doc_schema: Optional[dict]) -> Optional[dict]:
    """Vertex responseSchema용 스키마 정규화"""
    if not doc_schema or not isinstance(doc_schema, dict):
        return None

    def norm_type(t: Optional[str]) -> Optional[str]:
        if not t:
            return None
        t = str(t).lower().strip()
        mapping = {
            "object": "object",
            "array": "array",
            "string": "string",
            "integer": "integer",
            "number": "number",
            "boolean": "boolean",
        }
        return mapping.get(t, t)

    def walk(s: Any) -> Any:
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
    """
    ✅ Vertex AI (Gemini) REST 호출
    - service account JSON을 secrets에 넣는 방식 (Streamlit Cloud 호환)
    - responseMimeType/responseSchema로 JSON 강제(가능한 경우)
    - Groq는 백업(옵션)
    """
    def __init__(self):
        g = st.secrets.get("general", {})
        v = st.secrets.get("vertex", {})

        self.groq_key = g.get("GROQ_API_KEY")
        self.project_id = v.get("PROJECT_ID")
        self.location = v.get("LOCATION", "asia-northeast3")

        self.vertex_models = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash-001",
        ]

        self.creds = None
        sa_raw = v.get("SERVICE_ACCOUNT_JSON")
        if sa_raw and service_account is not None:
            try:
                sa_info = json.loads(sa_raw) if isinstance(sa_raw, str) else sa_raw
                self.creds = service_account.Credentials.from_service_account_info(
                    sa_info,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
            except Exception:
                self.creds = None

        self.groq_client = Groq(api_key=self.groq_key) if (Groq and self.groq_key) else None

    def _vertex_generate(
        self,
        prompt: str,
        model_name: str,
        response_mime_type: Optional[str] = None,
        response_schema: Optional[dict] = None,
    ) -> str:
        if not (self.creds and self.project_id and self.location and GoogleAuthRequest):
            raise Exception("Vertex AI credentials/project/location not configured")

        if not self.creds.valid or self.creds.expired:
            self.creds.refresh(GoogleAuthRequest())

        model_path = f"projects/{self.project_id}/locations/{self.location}/publishers/google/models/{model_name}"
        url = f"https://aiplatform.googleapis.com/v1/{model_path}:generateContent"

        gen_cfg: Dict[str, Any] = {"temperature": 0.2, "maxOutputTokens": 2048}
        if response_mime_type:
            gen_cfg["responseMimeType"] = response_mime_type
        if response_schema:
            gen_cfg["responseSchema"] = response_schema

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg,
        }

        headers = {
            "Authorization": f"Bearer {self.creds.token}",
            "Content-Type": "application/json",
        }

        r = http_post(url, json_body=payload, headers=headers, timeout=30, retries=1)
        data = r.json()

        if isinstance(data, dict) and data.get("error"):
            raise Exception(data["error"].get("message", "Vertex error"))

        try:
            return data["candidates"][0]["content"]["parts"][0].get("text", "") or ""
        except Exception:
            return ""

    def _generate_groq(self, prompt: str) -> str:
        if not self.groq_client:
            return ""
        try:
            completion = self.groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            return completion.choices[0].message.content or ""
        except Exception:
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

        return "시스템 오류: LLM 연결 실패 (Vertex/Groq 설정 확인 필요)"

    def generate_json(self, prompt: str, schema: Optional[dict] = None) -> Optional[dict]:
        response_schema = _vertex_schema_from_doc_schema(schema)

        # 1) Vertex: JSON 강제
        for m in self.vertex_models:
            try:
                txt = (self._vertex_generate(
                    prompt=prompt,
                    model_name=m,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                ) or "").strip()
                if txt:
                    return json.loads(txt)
            except Exception:
                continue

        # 2) 백업 파싱
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

        txt = self.generate_text(prompt + "\n\n반드시 JSON만 출력. 설명/서론/코드블록/마크다운 금지.")
        j = _try_parse(txt)
        if j is not None:
            return j

        txt2 = self.generate_text(
            "너의 출력은 파서로 바로 json.loads() 될 예정이다.\n"
            "따라서 순수 JSON 외의 문자는 1글자도 출력하면 안 된다.\n\n" + prompt
        )
        return _try_parse(txt2)


class SearchService:
    """뉴스 중심 경량 검색(네이버 API + 캐시)"""
    def _extract_keywords_llm(self, situation: str) -> str:
        prompt = f"상황: '{situation}'\n뉴스 검색을 위한 핵심 키워드 2개만 콤마로 구분해 출력."
        try:
            res = (llm_service.generate_text(prompt) or "").strip()
            return re.sub(r'[".?]', "", res)
        except Exception:
            return situation[:20]

    def search_news(self, query: str, top_k: int = 3) -> str:
        try:
            return cached_naver_news(query=query, top_k=top_k)
        except Exception as e:
            return f"검색 중 오류: {str(e)}"

    def search_precedents(self, situation: str, top_k: int = 3) -> str:
        keywords = self._extract_keywords_llm(situation)
        return self.search_news(keywords, top_k=top_k)


class DatabaseService:
    """
    ✅ Supabase Auth 로그인 + 데이터 관리 (RLS 권장)
    - 로그인 성공 시: sb_access_token/sb_user_email/sb_user_id 저장
    - SERVICE_ROLE_KEY가 있으면 관리자 모드(전체조회/삭제 가능)
    """
    def __init__(self):
        s = st.secrets.get("supabase", {})
        self.url = s.get("SUPABASE_URL")
        self.anon_key = s.get("SUPABASE_ANON_KEY") or s.get("SUPABASE_KEY")
        self.service_key = s.get("SUPABASE_SERVICE_ROLE_KEY")  # optional

        self.is_active = False
        self.auth_client = None
        self.base_client = None

        if create_client is None:
            self.is_active = False
            return

        try:
            if self.url and self.anon_key:
                self.auth_client = create_client(self.url, self.anon_key)
                self.base_client = create_client(self.url, self.service_key or self.anon_key)
                self.is_active = True
        except Exception:
            self.is_active = False

    def is_logged_in(self) -> bool:
        return bool(st.session_state.get("sb_access_token")) and bool(st.session_state.get("sb_user_email"))

    def sign_in(self, email: str, password: str) -> dict:
        if not self.is_active or not self.auth_client:
            return {"ok": False, "msg": "Supabase 연결 실패"}
        try:
            resp = self.auth_client.auth.sign_in_with_password({"email": email, "password": password})

            session = getattr(resp, "session", None) or (resp.get("session") if isinstance(resp, dict) else None)
            user = getattr(resp, "user", None) or (resp.get("user") if isinstance(resp, dict) else None)

            access_token = getattr(session, "access_token", None) if session else None
            refresh_token = getattr(session, "refresh_token", None) if session else None
            user_email = getattr(user, "email", None) if user else None
            user_id = getattr(user, "id", None) if user else None

            if not access_token or not user_email:
                return {"ok": False, "msg": "로그인 응답 파싱 실패(토큰 없음)"}

            st.session_state["sb_access_token"] = access_token
            st.session_state["sb_refresh_token"] = refresh_token or ""
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

    def _client_with_token(self, token: str):
        """
        supabase-py 버전에 따라 토큰 적용 방식이 다름.
        최대한 많은 케이스를 커버하는 fallback 체인.
        """
        c = self.base_client
        if not c or not token:
            return None

        try:
            if hasattr(c, "postgrest") and hasattr(c.postgrest, "auth"):
                c.postgrest.auth(token)
                return c
        except Exception:
            pass

        try:
            if hasattr(c, "_postgrest") and hasattr(c._postgrest, "auth"):
                c._postgrest.auth(token)
                return c
        except Exception:
            pass

        try:
            from supabase.lib.client_options import ClientOptions  # type: ignore
            opts = ClientOptions(headers={"Authorization": f"Bearer {token}", "apikey": self.anon_key})
            return create_client(self.url, self.anon_key, options=opts)
        except Exception:
            pass

        return c

    def _get_db_client(self):
        if not self.is_active:
            return None

        if self.service_key:
            return self.base_client

        token = st.session_state.get("sb_access_token")
        if not token:
            return None
        return self._client_with_token(token)

    def _pack_summary(self, res: dict, followup: dict) -> dict:
        return {
            "meta": res.get("meta"),
            "strategy": res.get("strategy"),
            "search_initial": res.get("search"),
            "law_initial": res.get("law"),
            "document_content": res.get("doc"),
            "followup": followup,
            "timings": res.get("timings"),
        }

    def insert_initial_report(self, res: dict) -> dict:
        c = self._get_db_client()
        if not c:
            return {"ok": False, "msg": "DB 저장 불가(로그인 필요 또는 RLS/권한 설정 필요)", "id": None}
        try:
            followup = {"count": 0, "messages": [], "extra_context": ""}
            data = {
                "situation": res.get("situation", ""),
                "law_name": res.get("law", ""),
                "summary": self._pack_summary(res, followup),
                "user_email": st.session_state.get("sb_user_email") or None,
                "user_id": st.session_state.get("sb_user_id") or None,
            }
            resp = c.table("law_reports").insert(data).execute()
            inserted_id = None
            try:
                d = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
                if isinstance(d, list) and d:
                    inserted_id = d[0].get("id")
            except Exception:
                inserted_id = None
            return {"ok": True, "msg": "DB 저장 성공", "id": inserted_id}
        except Exception as e:
            return {"ok": False, "msg": f"DB 저장 실패: {e}", "id": None}

    def update_followup(self, report_id, res: dict, followup: dict) -> dict:
        c = self._get_db_client()
        if not c:
            return {"ok": False, "msg": "DB 업데이트 불가(로그인 필요 또는 권한설정 필요)"}

        summary = self._pack_summary(res, followup)

        if report_id is not None:
            try:
                c.table("law_reports").update({"summary": summary}).eq("id", report_id).execute()
                return {"ok": True, "msg": "DB 업데이트 성공"}
            except Exception:
                pass

        try:
            data = {
                "situation": res.get("situation", ""),
                "law_name": res.get("law", ""),
                "summary": summary,
                "user_email": st.session_state.get("sb_user_email") or None,
                "user_id": st.session_state.get("sb_user_id") or None,
            }
            c.table("law_reports").insert(data).execute()
            return {"ok": True, "msg": "DB 업데이트 실패 → 신규 저장(fallback) 완료"}
        except Exception as e:
            return {"ok": False, "msg": f"DB 업데이트/저장 실패: {e}"}

    def list_reports(self, limit: int = 50, keyword: str = "") -> list:
        c = self._get_db_client()
        if not c:
            return []
        try:
            q = c.table("law_reports").select("id, created_at, situation, law_name").order("created_at", desc=True).limit(limit)
            if keyword:
                q = q.ilike("situation", f"%{keyword}%")
            resp = q.execute()
            data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
            return data or []
        except Exception:
            return []

    def get_report(self, report_id: str) -> Optional[dict]:
        c = self._get_db_client()
        if not c:
            return None
        try:
            resp = c.table("law_reports").select("*").eq("id", report_id).limit(1).execute()
            data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
            if isinstance(data, list) and data:
                return data[0]
            return None
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
    def __init__(self):
        self.api_id = st.secrets.get("general", {}).get("LAW_API_ID")

    def _make_current_link(self, mst_id: str) -> Optional[str]:
        if not self.api_id or not mst_id:
            return None
        return f"https://www.law.go.kr/DRF/lawService.do?OC={self.api_id}&target=law&MST={mst_id}&type=HTML"

    def get_law_text(self, law_name: str, article_num: Optional[int] = None, return_link: bool = False):
        if not self.api_id:
            msg = "⚠️ API ID(OC)가 설정되지 않았습니다. (secrets.toml: [general] LAW_API_ID)"
            return (msg, None) if return_link else msg

        try:
            mst_id = cached_law_search(self.api_id, law_name) or ""
            if not mst_id:
                msg = f"🔍 '{law_name}'에 대한 검색 결과가 없습니다."
                return (msg, None) if return_link else msg
        except Exception as e:
            msg = f"API 검색 중 오류: {e}"
            return (msg, None) if return_link else msg

        current_link = self._make_current_link(mst_id)

        try:
            xml_text = cached_law_detail_xml(self.api_id, mst_id)
            root_detail = _safe_et_from_bytes(xml_text.encode("utf-8", errors="ignore"))

            if article_num:
                target = str(article_num)
                for article in root_detail.findall(".//조문단위"):
                    jo_num_tag = article.find("조문번호")
                    jo_content_tag = article.find("조문내용")
                    if jo_num_tag is None or jo_content_tag is None:
                        continue

                    current_num = (jo_num_tag.text or "").strip()
                    if current_num == target or current_num.startswith(target):
                        target_text = f"[{law_name} 제{current_num}조 전문]\n" + _escape((jo_content_tag.text or "").strip())
                        for hang in article.findall(".//항"):
                            hang_content = hang.find("항내용")
                            if hang_content is not None and (hang_content.text or "").strip():
                                target_text += f"\n  - {(hang_content.text or '').strip()}"
                        return (target_text, current_link) if return_link else target_text

            msg = f"✅ '{law_name}'이(가) 확인되었습니다.\n(상세 조문 자동 추출 실패 또는 조문번호 미지정)\n🔗 현행 원문: {current_link or '-'}"
            return (msg, current_link) if return_link else msg

        except Exception as e:
            msg = f"상세 법령 파싱 실패: {e}"
            return (msg, current_link) if return_link else msg


# ==========================================
# 4) Global Instances
# ==========================================
@st.cache_resource(show_spinner=False)
def _get_services():
    return LLMService(), SearchService(), DatabaseService(), LawOfficialService()

llm_service, search_service, db_service, law_api_service = _get_services()


# ==========================================
# 5) Agents
# ==========================================
class LegalAgents:
    @staticmethod
    def researcher(situation: str) -> str:
        prompt_extract = f"""
상황: "{situation}"

위 민원 처리를 위해 법적 근거로 삼아야 할 핵심 대한민국 법령과 조문 번호를
**중요도 순으로 최대 3개까지** JSON 리스트로 추출하시오.

형식: [{{"law_name": "도로교통법", "article_num": 32}}, ...]
* 법령명은 정식 명칭 사용. 조문 번호 불명확하면 null.
"""
        search_targets: List[Dict[str, Any]] = []
        try:
            extracted = llm_service.generate_json(prompt_extract)
            if isinstance(extracted, list):
                search_targets = extracted
            elif isinstance(extracted, dict):
                search_targets = [extracted]
        except Exception:
            search_targets = [{"law_name": "도로교통법", "article_num": None}]

        if not search_targets:
            search_targets = [{"law_name": "도로교통법", "article_num": None}]

        report_lines: List[str] = []
        api_success_count = 0

        report_lines.append(f"🔍 **AI가 식별한 핵심 법령 ({len(search_targets)}건)**")
        report_lines.append("---")

        def fetch_one(idx: int, item: Dict[str, Any]):
            law_name = str(item.get("law_name") or "관련법령").strip()
            article_num = item.get("article_num")
            art = None
            try:
                if article_num is not None and str(article_num).strip().isdigit():
                    art = int(article_num)
            except Exception:
                art = None

            law_text, current_link = law_api_service.get_law_text(law_name, art, return_link=True)
            return idx, law_name, art, law_text, current_link

        results: List[Tuple[int, str, Optional[int], str, Optional[str]]] = []
        try:
            with ThreadPoolExecutor(max_workers=min(LAW_MAX_WORKERS, max(1, len(search_targets)))) as ex:
                futures = [ex.submit(fetch_one, idx, item) for idx, item in enumerate(search_targets)]
                for f in as_completed(futures):
                    results.append(f.result())
            results.sort(key=lambda x: x[0])
        except Exception:
            results = [fetch_one(idx, item) for idx, item in enumerate(search_targets)]

        for idx, law_name, art, law_text, current_link in results:
            error_keywords = ["검색 결과가 없습니다", "오류", "API ID", "실패", "파싱 실패"]
            is_success = not any(k in (law_text or "") for k in error_keywords)

            if is_success:
                api_success_count += 1
                law_title = f"[{law_name}]({current_link})" if current_link else law_name
                header = f"✅ **{idx+1}. {law_title} 제{art if art else '?'}조 (확인됨)**"
                content = law_text
            else:
                header = f"⚠️ **{idx+1}. {law_name} 제{art if art else '?'}조 (API 조회 실패)**"
                content = "(국가법령정보센터에서 해당 조문을 찾지 못했습니다. 법령명이 정확한지 확인이 필요합니다.)"

            report_lines.append(f"{header}\n{content}\n")

        final_report = "\n".join(report_lines)

        if api_success_count == 0:
            prompt_fallback = f"""
Role: 행정 법률 전문가
Task: 아래 상황에 적용될 법령과 조항을 찾아 설명하시오.
상황: "{situation}"

* 경고: 현재 외부 법령 API 연결이 원활하지 않습니다.
반드시 상단에 [AI 추론 결과]임을 명시하고 환각 가능성을 경고하시오.
"""
            ai_fallback_text = (llm_service.generate_text(prompt_fallback) or "").strip()
            return f"""⚠️ **[시스템 경고: API 조회 실패]**
(국가법령정보센터 연결 실패로 AI 지식 기반 답변입니다. **환각 가능성** 있으니 법제처 확인 필수)

--------------------------------------------------
{ai_fallback_text}"""

        return final_report

    @staticmethod
    def strategist(situation: str, legal_basis: str, search_results: str) -> str:
        prompt = f"""
당신은 행정 업무 베테랑 '주무관'입니다.

[민원 상황]: {situation}
[확보된 법적 근거]:
{legal_basis}

[유사 사례/판례]:
{search_results}

위 정보를 종합하여 민원 처리 방향(Strategy)을 수립하세요.
서론(인사말/공감/네 알겠습니다 등) 금지.

1. 처리 방향
2. 핵심 주의사항
3. 예상 반발 및 대응
"""
        return llm_service.generate_text(prompt)

    @staticmethod
    def clerk(situation: str, legal_basis: str) -> dict:
        today = datetime.now(KST)
        prompt = f"""
오늘: {today.strftime('%Y-%m-%d')}
상황: {situation}
법령: {legal_basis}
이행/의견제출 기간은 며칠인가?
숫자만 출력. 모르겠으면 15.
"""
        try:
            res = (llm_service.generate_text(prompt) or "").strip()
            m = re.search(r"\d{1,3}", res)
            days = int(m.group(0)) if m else 15
            days = max(1, min(days, 180))
        except Exception:
            days = 15

        deadline = today + timedelta(days=days)
        return {
            "today_str": today.strftime("%Y. %m. %d."),
            "deadline_str": deadline.strftime("%Y. %m. %d."),
            "days_added": days,
            "doc_num": f"행정-{today.strftime('%Y')}-{int(time.time())%1000:03d}호",
        }

    @staticmethod
    def drafter(situation: str, legal_basis: str, meta_info: dict, strategy: str) -> Optional[dict]:
        doc_schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "receiver": {"type": "string"},
                "body_paragraphs": {"type": "array", "items": {"type": "string"}},
                "department_head": {"type": "string"},
            },
            "required": ["title", "receiver", "body_paragraphs", "department_head"],
        }

        prompt = f"""
당신은 행정기관의 베테랑 서기입니다. 아래 정보를 바탕으로 완결된 공문서를 작성하세요.

[입력]
- 민원: {situation}
- 법적 근거: {legal_basis}
- 시행일자: {meta_info.get('today_str','')}
- 기한: {meta_info.get('deadline_str','')} ({meta_info.get('days_added','')}일)

[전략]
{strategy}

[원칙]
1) 본문에 법 조항 인용 필수
2) 구조: 경위 -> 법적 근거 -> 처분 내용 -> 이의제기 절차
3) 개인정보 마스킹('OOO')
4) 반드시 JSON만 출력 (title/receiver/body_paragraphs/department_head)
"""
        doc = llm_service.generate_json(prompt, schema=doc_schema)

        # 최후 방어: 파싱 실패 시 최소 템플릿
        if not isinstance(doc, dict):
            return {
                "title": "공문(초안)",
                "receiver": "수신자 참조",
                "body_paragraphs": [
                    "1. (경위) OOO",
                    "2. (법적 근거) OOO",
                    "3. (처분/안내) OOO",
                    "4. (이의제기) OOO",
                ],
                "department_head": "행정기관장",
            }

        bp = doc.get("body_paragraphs")
        if isinstance(bp, str):
            doc["body_paragraphs"] = [bp]
        elif not isinstance(bp, list):
            doc["body_paragraphs"] = []

        for k in ["title", "receiver", "department_head"]:
            if k not in doc or not isinstance(doc.get(k), str):
                doc[k] = ""

        return doc


# ==========================================
# 6) Workflow
# ==========================================
def run_workflow(user_input: str) -> dict:
    log_placeholder = st.empty()
    logs: List[str] = []
    timings: Dict[str, float] = {}

    def add_log(msg: str, style: str = "sys"):
        logs.append(f"<div class='agent-log log-{style}'>{_escape(msg)}</div>")
        log_placeholder.markdown("".join(logs), unsafe_allow_html=True)

    t0 = time.perf_counter()

    add_log("🔍 Phase 1: 법령 리서치 중...(병렬)", "legal")
    t = time.perf_counter()
    legal_basis = LegalAgents.researcher(user_input)
    timings["law_research_sec"] = round(time.perf_counter() - t, 3)
    add_log(f"📜 법적 근거 발견 완료 ({timings['law_research_sec']}s)", "legal")

    add_log("🟩 네이버 검색 엔진 가동...(캐시)", "search")
    t = time.perf_counter()
    try:
        search_results = search_service.search_precedents(user_input)
    except Exception:
        search_results = "검색 모듈 미연결 (건너뜀)"
    timings["news_search_sec"] = round(time.perf_counter() - t, 3)

    add_log(f"🧠 Phase 2: AI 주무관이 처리 방향 수립... ({timings['news_search_sec']}s 검색완료)", "strat")
    t = time.perf_counter()
    strategy = LegalAgents.strategist(user_input, legal_basis, search_results)
    timings["strategy_sec"] = round(time.perf_counter() - t, 3)

    add_log("📅 Phase 3: 기한 산정...", "calc")
    t = time.perf_counter()
    meta_info = LegalAgents.clerk(user_input, legal_basis)
    timings["deadline_calc_sec"] = round(time.perf_counter() - t, 3)

    add_log("✍️ Phase 4: 공문서 생성(JSON)...", "draft")
    t = time.perf_counter()
    doc_data = LegalAgents.drafter(user_input, legal_basis, meta_info, strategy)
    timings["draft_sec"] = round(time.perf_counter() - t, 3)

    timings["total_sec"] = round(time.perf_counter() - t0, 3)
    log_placeholder.empty()

    return {
        "situation": user_input,
        "doc": doc_data,
        "meta": meta_info,
        "law": legal_basis,
        "search": search_results,
        "strategy": strategy,
        "timings": timings,
    }


# ==========================================
# 7) Follow-up Chat
# ==========================================
def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def build_case_context(res: dict) -> str:
    situation = res.get("situation", "")
    law_txt = _strip_html(res.get("law", ""))
    news_txt = _strip_html(res.get("search", ""))
    strategy = res.get("strategy", "")
    doc = res.get("doc") or {}

    body_paras = doc.get("body_paragraphs", [])
    if isinstance(body_paras, str):
        body_paras = [body_paras]
    body = "\n".join([f"- {p}" for p in body_paras])

    ctx = f"""
[케이스 컨텍스트]
1) 민원 상황(원문)
{situation}

2) 적용 법령/조문(이미 확인된 내용)
{law_txt}

3) 관련 뉴스/사례(이미 조회된 내용)
{news_txt}

4) 업무 처리 방향(Strategy)
{strategy}

5) 생성된 공문서(요약)
- 제목: {doc.get('title','')}
- 수신: {doc.get('receiver','')}
- 본문:
{body}
- 발신: {doc.get('department_head','')}

[규칙]
- 기본 답변은 위 컨텍스트 범위에서만 작성.
- 컨텍스트에 없는 법령/사례를 단정하지 말 것.
- 사용자가 “근거 더 / 다른 조문 / 뉴스 더” 요청하면 그때만 추가 조회.
"""
    return ctx.strip()


def needs_tool_call(user_msg: str) -> dict:
    t = (user_msg or "").lower()
    law_triggers = ["근거", "조문", "법령", "몇 조", "원문", "현행", "추가 조항", "다른 조문", "전문", "절차법", "행정절차"]
    news_triggers = ["뉴스", "사례", "판례", "기사", "보도", "최근", "유사", "선례"]
    return {"need_law": any(k in t for k in law_triggers), "need_news": any(k in t for k in news_triggers)}


def plan_tool_calls_llm(user_msg: str, situation: str, known_law_text: str) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "need_law": {"type": "boolean"},
            "law_name": {"type": "string"},
            "article_num": {"type": "integer"},
            "need_news": {"type": "boolean"},
            "news_query": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["need_law", "law_name", "article_num", "need_news", "news_query", "reason"],
    }

    prompt = f"""
너는 행정업무 보조 에이전트다. 사용자의 후속 질문을 보고, 추가 조회가 필요하면 계획을 JSON으로 만든다.

[민원 상황]
{situation}

[이미 확보된 적용 법령 텍스트]
{known_law_text[:2500]}

[사용자 질문]
{user_msg}

[출력 규칙]
- 추가 법령 조회 필요: need_law=true, law_name=정식 법령명 1개, article_num=정수(모르면 0)
- 추가 뉴스 조회 필요: need_news=true, news_query=2~4단어 키워드(콤마 가능)
- 불필요하면 need_law/need_news=false
- 반드시 JSON만 출력
"""
    plan = llm_service.generate_json(prompt, schema=schema) or {}
    if not isinstance(plan, dict):
        return {"need_law": False, "law_name": "", "article_num": 0, "need_news": False, "news_query": "", "reason": "parse failed"}

    try:
        plan["article_num"] = int(plan.get("article_num") or 0)
    except Exception:
        plan["article_num"] = 0

    plan["law_name"] = str(plan.get("law_name") or "").strip()
    plan["news_query"] = str(plan.get("news_query") or "").strip()
    plan["reason"] = str(plan.get("reason") or "").strip()

    plan["need_law"] = bool(plan.get("need_law"))
    plan["need_news"] = bool(plan.get("need_news"))
    return plan


def answer_followup(case_context: str, extra_context: str, chat_history: list, user_msg: str) -> str:
    hist = chat_history[-8:]
    hist_txt = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in hist]) if hist else "(없음)"

    prompt = f"""
너는 '케이스 고정 행정 후속 Q&A 챗봇'이다.

{case_context}

[추가 조회 결과(있으면)]
{extra_context if extra_context else "(없음)"}

[대화 히스토리(최근)]
{hist_txt}

[사용자 질문]
{user_msg}

[답변 규칙]
- 케이스 컨텍스트/추가 조회 결과 범위에서만 답한다.
- 모르면 모른다고 하고, 필요한 추가 조회 종류(법령/뉴스)를 구체적으로 말한다.
- 서론 없이 실무형으로.
"""
    return llm_service.generate_text(prompt)


def render_followup_chat(res: dict):
    st.session_state.setdefault("case_id", None)
    st.session_state.setdefault("followup_count", 0)
    st.session_state.setdefault("followup_messages", [])
    st.session_state.setdefault("followup_extra_context", "")
    st.session_state.setdefault("report_id", None)

    current_case_id = (res.get("meta") or {}).get("doc_num", "") or "case"
    if st.session_state["case_id"] != current_case_id:
        st.session_state["case_id"] = current_case_id
        st.session_state["followup_count"] = 0
        st.session_state["followup_messages"] = []
        st.session_state["followup_extra_context"] = ""

    remain = max(0, MAX_FOLLOWUP_Q - st.session_state["followup_count"])
    st.info(f"후속 질문 가능 횟수: **{remain}/{MAX_FOLLOWUP_Q}**")

    if remain == 0:
        st.warning("후속 질문 한도(5회)를 모두 사용했습니다. (추가 질문 불가)")
        return

    for m in st.session_state["followup_messages"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    user_q = st.chat_input("공문 결과를 바탕으로 후속 질문 (최대 5회)")
    if not user_q:
        return

    st.session_state["followup_messages"].append({"role": "user", "content": user_q})
    st.session_state["followup_count"] += 1

    with st.chat_message("user"):
        st.markdown(user_q)

    case_context = build_case_context(res)

    extra_ctx = st.session_state.get("followup_extra_context", "")
    tool_need = needs_tool_call(user_q)

    if tool_need["need_law"] or tool_need["need_news"]:
        plan = plan_tool_calls_llm(user_q, res.get("situation", ""), _strip_html(res.get("law", "")))

        if plan.get("need_law") and plan.get("law_name"):
            art = plan.get("article_num", 0)
            art = art if art > 0 else None
            law_text, law_link = law_api_service.get_law_text(plan["law_name"], art, return_link=True)

            extra_ctx += f"\n\n[추가 법령 조회]\n- 요청: {plan['law_name']} / 제{art if art else '?'}조\n{_strip_html(law_text)}"
            if law_link:
                extra_ctx += f"\n(현행 원문 링크: {law_link})"

        if plan.get("need_news") and plan.get("news_query"):
            news_txt = search_service.search_news(plan["news_query"])
            extra_ctx += f"\n\n[추가 뉴스 조회]\n- 검색어: {plan['news_query']}\n{_strip_html(news_txt)}"

        st.session_state["followup_extra_context"] = extra_ctx

    with st.chat_message("assistant"):
        with st.spinner("후속 답변 생성 중..."):
            ans = answer_followup(
                case_context=case_context,
                extra_context=st.session_state.get("followup_extra_context", ""),
                chat_history=st.session_state["followup_messages"],
                user_msg=user_q,
            )
            st.markdown(ans)

    st.session_state["followup_messages"].append({"role": "assistant", "content": ans})

    followup_payload = {
        "count": st.session_state["followup_count"],
        "messages": st.session_state["followup_messages"],
        "extra_context": st.session_state.get("followup_extra_context", ""),
    }
    upd = db_service.update_followup(
        report_id=st.session_state.get("report_id"),
        res=res,
        followup=followup_payload,
    )
    if not upd.get("ok"):
        st.caption(f"DB 후속 저장 실패: {upd.get('msg')}")


# ==========================================
# 8) Login & Data Management UI
# ==========================================
def render_login_box():
    with st.expander("🔐 로그인 (Supabase Auth)", expanded=not db_service.is_logged_in()):
        if not db_service.is_active:
            st.error("Supabase 연결이 안 됐습니다. secrets 설정을 확인하세요.")
            return

        if db_service.is_logged_in():
            st.success(f"로그인됨: {st.session_state.get('sb_user_email')}")
            if st.button("로그아웃", use_container_width=True):
                out = db_service.sign_out()
                if out.get("ok"):
                    st.rerun()
                else:
                    st.error(out.get("msg"))
        else:
            email = st.text_input("이메일", key="login_email")
            pw = st.text_input("비밀번호", type="password", key="login_pw")
            if st.button("로그인", type="primary", use_container_width=True):
                r = db_service.sign_in(email, pw)
                if r.get("ok"):
                    st.rerun()
                else:
                    st.error(r.get("msg"))


def render_data_management_panel():
    with st.expander("🗂️ 데이터 관리 (조회/삭제/다운로드)", expanded=False):
        if not db_service.is_logged_in() and not db_service.service_key:
            st.info("로그인 후 사용 가능합니다. (또는 SERVICE_ROLE_KEY 설정 시 관리자 모드로 동작)")
            return

        colA, colB = st.columns([1, 1])
        with colA:
            keyword = st.text_input("상황 검색(키워드)", placeholder="예: 무단방치, 번호판, 과태료 ...")
        with colB:
            limit = st.slider("불러올 개수", 10, 200, 50, 10)

        rows = db_service.list_reports(limit=limit, keyword=keyword)
        if not rows:
            st.caption("조회 결과가 없습니다.")
            return

        options = []
        id_map = {}
        for r in rows:
            rid = r.get("id")
            created = (r.get("created_at") or "")[:19].replace("T", " ")
            sit = (r.get("situation") or "").replace("\n", " ")
            label = f"{created} | {str(rid)[:8]} | {sit[:60]}"
            options.append(label)
            id_map[label] = rid

        picked = st.selectbox("보고서 선택", options)
        report_id = id_map.get(picked)

        detail = db_service.get_report(report_id) if report_id else None
        if not detail:
            st.warning("상세 조회 실패")
            return

        st.markdown("#### 상세(JSON)")
        st.json(detail)

        jtxt = json.dumps(detail, ensure_ascii=False, indent=2)
        c1, c2 = st.columns([1, 1])
        with c1:
            st.download_button(
                "⬇️ JSON 다운로드",
                data=jtxt.encode("utf-8"),
                file_name=f"law_report_{report_id}.json",
                mime="application/json",
                use_container_width=True,
            )
        with c2:
            if st.button("🗑️ 삭제", use_container_width=True):
                r = db_service.delete_report(report_id)
                if r.get("ok"):
                    st.success("삭제 완료")
                    st.rerun()
                else:
                    st.error(r.get("msg"))


# ==========================================
# 9) UI
# ==========================================
def main():
    with st.sidebar:
        st.markdown("### ✅ 시스템 상태")
        g = st.secrets.get("general", {})
        v = st.secrets.get("vertex", {})
        s = st.secrets.get("supabase", {})

        st.write("법령 API:", "✅" if g.get("LAW_API_ID") else "❌")
        st.write("네이버 뉴스 API:", "✅" if (g.get("NAVER_CLIENT_ID") and g.get("NAVER_CLIENT_SECRET")) else "❌")
        st.write("Vertex SA JSON:", "✅" if v.get("SERVICE_ACCOUNT_JSON") else "❌")
        st.write("Supabase URL/KEY:", "✅" if (s.get("SUPABASE_URL") and (s.get("SUPABASE_ANON_KEY") or s.get("SUPABASE_KEY"))) else "❌")
        if db_service.service_key:
            st.caption("관리자 모드: SERVICE_ROLE_KEY 사용 중")
        st.caption("⚠️ 민감정보(성명/연락처/주소/차량번호)는 입력 금지")

    col_left, col_right = st.columns([1, 1.2])

    with col_left:
        render_login_box()
        render_data_management_panel()

        st.title("🏢 AI 행정관 Pro 충주시청")
        st.caption("문의 kim0395kk@korea.kr \n 세계최초 행정 Govable AI 에이전트")
        st.markdown("---")

        st.markdown("### 🗣️ 업무 지시")
        user_input = st.text_area(
            "업무 내용",
            height=150,
            placeholder="예시 \n- 상황: (무슨 일 / 어디 / 언제 / 증거 유무...) \n- 의도: (확인하고 싶은 쟁점: 요건/절차/근거) \n- 요청: (원하는 결과물: 공문 종류/회신/사전통지 등)",
            label_visibility="collapsed",
        )

        if st.button("⚡ 스마트 분석 시작", type="primary", use_container_width=True):
            if not user_input:
                st.warning("내용을 입력해주세요.")
            else:
                try:
                    with st.spinner("AI 에이전트 팀이 협업 중입니다..."):
                        res = run_workflow(user_input)

                        ins = db_service.insert_initial_report(res)
                        res["save_msg"] = ins.get("msg")
                        st.session_state["report_id"] = ins.get("id")

                        st.session_state["workflow_result"] = res
                except Exception as e:
                    st.error(f"시스템 오류 발생: {e}")

        if "workflow_result" in st.session_state:
            res = st.session_state["workflow_result"]
            st.markdown("---")

            if "성공" in (res.get("save_msg") or ""):
                st.success(f"✅ {res['save_msg']}")
            else:
                st.info(f"ℹ️ {res.get('save_msg','')}")

            t = res.get("timings") or {}
            if t:
                with st.expander("⏱️ 처리 소요시간(디버그)", expanded=False):
                    st.json(t)

            with st.expander("✅ [검토] 법령 및 유사 사례 확인", expanded=True):
                col1, col2 = st.columns(2)

                with col1:
                    st.markdown("**📜 적용 법령 (법령명 클릭 시 현행 원문 새창)**")
                    raw_law = res.get("law", "")

                    cleaned = raw_law.replace("&lt;", "<").replace("&gt;", ">")
                    cleaned = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", cleaned)
                    cleaned = re.sub(
                        r'\[([^\]]+)\]\(([^)]+)\)',
                        r'<a href="\2" target="_blank" style="color:#2563eb; text-decoration:none; font-weight:700;">\1</a>',
                        cleaned,
                    )
                    cleaned = cleaned.replace("---", "<br><br>").replace("\n", "<br>")

                    st.markdown(
                        f"""
                        <div style="
                            height: 300px;
                            overflow-y: auto;
                            padding: 15px;
                            border-radius: 8px;
                            border: 1px solid #e5e7eb;
                            background: #f8fafc;
                            font-family: 'Pretendard', sans-serif;
                            font-size: 0.9rem;
                            line-height: 1.6;
                            color: #334155;
                        ">
                        {cleaned}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                with col2:
                    st.markdown("**🟩 관련 뉴스/사례 (캐시 10분)**")
                    raw_news = res.get("search", "")

                    news_body = raw_news.replace("# ", "").replace("## ", "")
                    news_body = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", news_body)
                    news_html = re.sub(
                        r"\[([^\]]+)\]\(([^)]+)\)",
                        r'<a href="\2" target="_blank" style="color:#2563eb; text-decoration:none; font-weight:600;">\1</a>',
                        news_body,
                    )
                    news_html = news_html.replace("\n", "<br>")

                    st.markdown(
                        f"""
                        <div style="
                            height: 300px;
                            overflow-y: auto;
                            padding: 15px;
                            border-radius: 8px;
                            border: 1px solid #dbeafe;
                            background: #eff6ff;
                            font-family: 'Pretendard', sans-serif;
                            font-size: 0.9rem;
                            line-height: 1.6;
                            color: #1e3a8a;
                        ">
                        {news_html}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

            with st.expander("🧭 [방향] 업무 처리 가이드라인", expanded=True):
                st.markdown(res.get("strategy", ""))

    with col_right:
        if "workflow_result" in st.session_state:
            res = st.session_state["workflow_result"]
            doc = res.get("doc") or {}
            meta = res.get("meta", {})

            if doc:
                html_content = f"""
<div class="paper-sheet">
  <div class="stamp">직인생략</div>
  <div class="doc-header">{_escape(doc.get('title', '공 문 서'))}</div>
  <div class="doc-info">
    <span>문서번호: {_escape(meta.get('doc_num',''))}</span>
    <span>시행일자: {_escape(meta.get('today_str',''))}</span>
    <span>수신: {_escape(doc.get('receiver', '수신자 참조'))}</span>
  </div>
  <hr style="border: 1px solid black; margin-bottom: 30px;">
  <div class="doc-body">
"""
                paragraphs = doc.get("body_paragraphs", [])
                if isinstance(paragraphs, str):
                    paragraphs = [paragraphs]

                for p in paragraphs:
                    html_content += f"<p style='margin-bottom: 15px;'>{_escape(str(p))}</p>"

                html_content += f"""
  </div>
  <div class="doc-footer">{_escape(doc.get('department_head', '행정기관장'))}</div>
</div>
"""
                st.markdown(html_content, unsafe_allow_html=True)

                st.markdown("---")
                with st.expander("💬 [후속 질문] 케이스 고정 챗봇 (최대 5회)", expanded=True):
                    render_followup_chat(res)
            else:
                st.warning("공문 생성 결과(doc)가 비어 있습니다. (모델 JSON 출력 실패 가능)")
        else:
            st.markdown(
                """<div style='text-align: center; padding: 100px; color: #aaa; background: white; border-radius: 10px; border: 2px dashed #ddd;'>
<h3>📄 Document Preview</h3><p>왼쪽에서 업무를 지시하면<br>완성된 공문서가 여기에 나타납니다.</p></div>""",
                unsafe_allow_html=True,
            )


if __name__ == "__main__":
    main()

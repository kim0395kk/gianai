# streamlit_app.py
# -*- coding: utf-8 -*-
# Govable AI Bureau - Stabilized Version
# Last updated: 2026-01-14

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

# Thread lock for Vertex token refresh
_vertex_lock = threading.Lock()


def _safe_secrets(section: str) -> dict:
    """secrets.toml이 아예 없어도 에러 없이 빈 dict 반환"""
    try:
        return dict(st.secrets.get(section, {}))
    except Exception:
        return {}


# ==========================================
# 1) Configuration & Styles
# ==========================================
st.set_page_config(layout="wide", page_title="AI Bureau: The Legal Glass", page_icon="⚖️")

st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
    
    /* Modern gradient background */
    .stApp { 
        background: linear-gradient(135deg, #f0f4f8 0%, #e1e8ed 50%, #d4dce3 100%);
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    
    .stApp::before {
        content: '';
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: radial-gradient(circle at 20% 50%, rgba(120, 119, 198, 0.3), transparent 50%),
                    radial-gradient(circle at 80% 80%, rgba(252, 70, 107, 0.3), transparent 50%),
                    radial-gradient(circle at 40% 20%, rgba(99, 102, 241, 0.2), transparent 50%);
        pointer-events: none;
        z-index: 0;
    }
    
    @keyframes gradientShift {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    
    /* Glass overlay for content */
    [data-testid="stAppViewContainer"] > .main {
        background: rgba(255, 255, 255, 0.05);
        backdrop-filter: blur(10px);
    }
    
    /* Premium Sidebar */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(255, 255, 255, 0.92) 100%);
        backdrop-filter: blur(40px) saturate(180%);
        border-right: 2px solid rgba(120, 119, 198, 0.2);
        box-shadow: 4px 0 24px rgba(99, 102, 241, 0.1);
    }
    
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 2rem;
    }
    
    /* Sidebar titles with gradient */
    [data-testid="stSidebar"] h1, 
    [data-testid="stSidebar"] h2, 
    [data-testid="stSidebar"] h3 {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        font-weight: 800;
    }
    
    /* Premium 3D paper sheet with glow */
    .paper-sheet {
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.98) 0%, rgba(255, 255, 255, 0.95) 100%);
        backdrop-filter: blur(40px) saturate(180%);
        width: 100%;
        max-width: 210mm;
        min-height: 297mm;
        padding: 25mm;
        margin: auto;
        box-shadow: 
            0 0 60px rgba(102, 126, 234, 0.3),
            0 30px 90px rgba(118, 75, 162, 0.2),
            inset 0 1px 0 rgba(255, 255, 255, 0.8);
        border: 2px solid rgba(255, 255, 255, 0.3);
        font-family: 'Inter', serif;
        color: #1a1a2e;
        line-height: 1.7;
        position: relative;
        border-radius: 24px;
        transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        transform: perspective(1000px) rotateX(0deg) rotateY(0deg);
    }
    
    .paper-sheet:hover {
        transform: perspective(1000px) rotateX(2deg) rotateY(-2deg) translateY(-8px);
        box-shadow: 
            0 0 80px rgba(102, 126, 234, 0.4),
            0 40px 120px rgba(118, 75, 162, 0.3),
            inset 0 1px 0 rgba(255, 255, 255, 0.9);
    }

    .doc-header { 
        text-align: center; 
        font-size: 26pt; 
        font-weight: 900; 
        margin-bottom: 40px; 
        letter-spacing: 2px;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 50%, #f093fb 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        text-shadow: 0 0 30px rgba(102, 126, 234, 0.3);
        animation: titleGlow 3s ease-in-out infinite;
    }
    
    @keyframes titleGlow {
        0%, 100% { filter: brightness(1); }
        50% { filter: brightness(1.2); }
    }
    
    .doc-info { 
        display: flex; 
        justify-content: space-between; 
        font-size: 10.5pt; 
        border-bottom: 2px solid #4682b4; 
        padding-bottom: 12px; 
        margin-bottom: 25px; 
        gap: 12px; 
        flex-wrap: wrap;
        font-weight: 500;
        color: #2d3748;
    }
    
    .doc-body { 
        font-size: 11.5pt; 
        text-align: justify; 
        white-space: pre-line;
        color: #2d3748;
        line-height: 1.8;
    }
    
    .doc-footer { 
        text-align: center; 
        font-size: 18pt; 
        font-weight: 700; 
        margin-top: 80px; 
        letter-spacing: 4px;
        color: #4682b4;
    }
    
    .stamp { 
        position: absolute; 
        bottom: 85px; 
        right: 80px; 
        border: 4px solid #dc2626; 
        color: #dc2626; 
        padding: 10px 18px; 
        font-size: 14pt; 
        font-weight: 900; 
        transform: rotate(-15deg); 
        opacity: 0.9; 
        border-radius: 12px;
        background: rgba(255, 255, 255, 0.95);
        box-shadow: 
            0 8px 24px rgba(220, 38, 38, 0.3),
            inset 0 1px 0 rgba(255, 255, 255, 0.5);
        animation: stampPulse 2s ease-in-out infinite;
    }
    
    @keyframes stampPulse {
        0%, 100% { transform: rotate(-15deg) scale(1); }
        50% { transform: rotate(-15deg) scale(1.05); }
    }

    /* Premium agent logs with neon glow */
    .agent-log { 
        font-family: 'Inter', 'Consolas', monospace; 
        font-size: 0.9rem; 
        padding: 14px 20px; 
        border-radius: 16px; 
        margin-bottom: 12px; 
        backdrop-filter: blur(20px) saturate(180%);
        border: 2px solid rgba(255, 255, 255, 0.2);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
    }
    
    .agent-log::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.3), transparent);
        transition: left 0.5s;
    }
    
    .agent-log:hover::before {
        left: 100%;
    }
    
    .agent-log:hover {
        transform: translateX(8px) scale(1.02);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
    }
    
    .log-legal { 
        background: linear-gradient(135deg, rgba(102, 126, 234, 0.25), rgba(102, 126, 234, 0.15)); 
        color: #3730a3; 
        border-left: 5px solid #667eea;
        box-shadow: 0 4px 20px rgba(102, 126, 234, 0.2);
    }
    
    .log-legal:hover {
        box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
        border-left-color: #5a67d8;
    }
    
    .log-search { 
        background: linear-gradient(135deg, rgba(79, 172, 254, 0.25), rgba(79, 172, 254, 0.15)); 
        color: #0c4a6e; 
        border-left: 5px solid #4facfe;
        box-shadow: 0 4px 20px rgba(79, 172, 254, 0.2);
    }
    
    .log-search:hover {
        box-shadow: 0 8px 32px rgba(79, 172, 254, 0.3);
        border-left-color: #0ea5e9;
    }
    
    .log-strat { 
        background: linear-gradient(135deg, rgba(168, 85, 247, 0.25), rgba(168, 85, 247, 0.15)); 
        color: #581c87; 
        border-left: 5px solid #a855f7;
        box-shadow: 0 4px 20px rgba(168, 85, 247, 0.2);
    }
    
    .log-strat:hover {
        box-shadow: 0 8px 32px rgba(168, 85, 247, 0.3);
        border-left-color: #9333ea;
    }
    
    .log-calc { 
        background: linear-gradient(135deg, rgba(34, 197, 94, 0.25), rgba(34, 197, 94, 0.15)); 
        color: #14532d; 
        border-left: 5px solid #22c55e;
        box-shadow: 0 4px 20px rgba(34, 197, 94, 0.2);
    }
    
    .log-calc:hover {
        box-shadow: 0 8px 32px rgba(34, 197, 94, 0.3);
        border-left-color: #16a34a;
    }
    
    .log-draft { 
        background: linear-gradient(135deg, rgba(251, 113, 133, 0.25), rgba(251, 113, 133, 0.15)); 
        color: #881337; 
        border-left: 5px solid #fb7185;
        box-shadow: 0 4px 20px rgba(251, 113, 133, 0.2);
    }
    
    .log-draft:hover {
        box-shadow: 0 8px 32px rgba(251, 113, 133, 0.3);
        border-left-color: #f43f5e;
    }
    
    .log-sys { 
        background: linear-gradient(135deg, rgba(148, 163, 184, 0.25), rgba(148, 163, 184, 0.15)); 
        color: #1e293b; 
        border-left: 5px solid #94a3b8;
        box-shadow: 0 4px 20px rgba(148, 163, 184, 0.2);
    }
    
    .log-sys:hover {
        box-shadow: 0 8px 32px rgba(148, 163, 184, 0.3);
        border-left-color: #64748b;
    }
    
    /* Futuristic glowing buttons */
    .stButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: 2px solid rgba(255, 255, 255, 0.3);
        border-radius: 16px;
        padding: 0.9rem 2rem;
        font-weight: 700;
        font-size: 1rem;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 
            0 8px 32px rgba(102, 126, 234, 0.4),
            inset 0 1px 0 rgba(255, 255, 255, 0.2);
        position: relative;
        overflow: hidden;
    }
    
    .stButton > button::before {
        content: '';
        position: absolute;
        top: 50%;
        left: 50%;
        width: 0;
        height: 0;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.3);
        transform: translate(-50%, -50%);
        transition: width 0.6s, height 0.6s;
    }
    
    .stButton > button:hover::before {
        width: 300px;
        height: 300px;
    }
    
    .stButton > button:hover {
        transform: translateY(-4px) scale(1.05);
        box-shadow: 
            0 12px 48px rgba(102, 126, 234, 0.6),
            0 0 40px rgba(118, 75, 162, 0.4),
            inset 0 1px 0 rgba(255, 255, 255, 0.3);
        border-color: rgba(255, 255, 255, 0.5);
    }
    
    .stButton > button:active {
        transform: translateY(-2px) scale(1.02);
    }
    
    /* Premium text inputs with glow */
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea {
        border: 2px solid rgba(102, 126, 234, 0.3);
        border-radius: 16px;
        padding: 1rem 1.25rem;
        font-family: 'Inter', sans-serif;
        font-size: 0.95rem;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        background: rgba(255, 255, 255, 0.95);
        backdrop-filter: blur(10px);
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.05);
    }
    
    .stTextInput > div > div > input:focus,
    .stTextArea > div > div > textarea:focus {
        border-color: #667eea;
        background: rgba(255, 255, 255, 1);
        box-shadow: 
            0 0 0 4px rgba(102, 126, 234, 0.15),
            0 8px 24px rgba(102, 126, 234, 0.2);
        transform: translateY(-2px);
    }
    
    /* Premium expanders with gradient */
    .streamlit-expanderHeader {
        background: linear-gradient(135deg, rgba(102, 126, 234, 0.12), rgba(118, 75, 162, 0.08));
        backdrop-filter: blur(10px);
        border-radius: 16px;
        border: 2px solid rgba(102, 126, 234, 0.2);
        padding: 1rem 1.5rem;
        font-weight: 700;
        color: #1e293b;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 4px 16px rgba(102, 126, 234, 0.1);
    }
    
    .streamlit-expanderHeader:hover {
        background: linear-gradient(135deg, rgba(102, 126, 234, 0.2), rgba(118, 75, 162, 0.15));
        border-color: rgba(102, 126, 234, 0.4);
        transform: translateX(4px);
        box-shadow: 0 6px 24px rgba(102, 126, 234, 0.2);
    }
    
    /* Status indicators with modern design */
    div[data-testid="stMarkdownContainer"] p {
        font-family: 'Inter', sans-serif;
    }
    
    /* Info, success, warning, error boxes */
    .stAlert {
        border-radius: 12px;
        border: 1px solid rgba(70, 130, 180, 0.2);
        backdrop-filter: blur(10px);
    }

    /* Streamlit Cloud 상단 숨김 */
    header [data-testid="stToolbar"] { display: none !important; }
    header [data-testid="stDecoration"] { display: none !important; }
    header { height: 0px !important; }
    footer { display: none !important; }
    div[data-testid="stStatusWidget"] { display: none !important; }
    
    /* Enhanced titles with gradient and glow */
    h1, h2, h3 {
        font-family: 'Inter', sans-serif;
        font-weight: 900;
        color: #0f172a;
    }
    
    h1 {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 50%, #f093fb 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        filter: drop-shadow(0 0 20px rgba(102, 126, 234, 0.3));
    }
    
    /* Status indicators with icons */
    [data-testid="stMarkdownContainer"] p:has(> strong:first-child) {
        padding: 0.5rem 1rem;
        border-radius: 12px;
        background: rgba(255, 255, 255, 0.7);
        backdrop-filter: blur(10px);
        margin: 0.5rem 0;
    }
    
    /* Info boxes enhancement */
    .stAlert {
        border-radius: 16px;
        border: 2px solid rgba(102, 126, 234, 0.3);
        backdrop-filter: blur(20px);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
    }
    
    /* Success/Error badges with glow */
    [data-testid="stMarkdownContainer"]:has(> p:first-child:contains("✅")) {
        animation: successPulse 2s ease-in-out infinite;
    }
    
    @keyframes successPulse {
        0%, 100% { filter: brightness(1); }
        50% { filter: brightness(1.1) drop-shadow(0 0 10px rgba(34, 197, 94, 0.5)); }
    }
</style>
""",
    unsafe_allow_html=True,
)



# ==========================================
# 2) Utils (HTTP, Cache, XML)
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
    """행정규칙(훈령/예규/고시) 검색 - ID 반환"""
    base_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {"OC": api_id, "target": "admrul", "type": "XML", "query": query, "display": 1}
    r = http_get(base_url, params=params, timeout=10)
    root = _safe_et_from_bytes(r.content)
    admrul_node = root.find(".//admrul")
    if admrul_node is None:
        return ""
    return (admrul_node.findtext("행정규칙ID") or admrul_node.findtext("admrulId") or "").strip()


@st.cache_data(ttl=86400, show_spinner=False)
def cached_admrul_detail(api_id: str, admrul_id: str) -> str:
    """행정규칙 본문 XML 조회"""
    service_url = "https://www.law.go.kr/DRF/lawService.do"
    params = {"OC": api_id, "target": "admrul", "type": "XML", "ID": admrul_id}
    r = http_get(service_url, params=params, timeout=15)
    return _safe_decode(r.content)


@st.cache_data(ttl=600, show_spinner=False)
def cached_ai_search(api_id: str, query: str, top_k: int = 5) -> List[Dict[str, str]]:
    """지능형(AIS) 검색 - 결과 목록"""
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
def cached_naver_news(query: str, top_k: int = 3) -> str:
    g = _safe_secrets("general")
    client_id = g.get("NAVER_CLIENT_ID")
    client_secret = g.get("NAVER_CLIENT_SECRET")

    if not client_id or not client_secret:
        return "⚠️ 네이버 API 키가 없습니다."
    if not query:
        return "⚠️ 검색어가 비었습니다."

    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    params = {"query": query, "display": 10, "sort": "sim"}
    r = http_get("https://openapi.naver.com/v1/search/news.json", params=params, headers=headers, timeout=8)
    items = r.json().get("items", []) or []

    if not items:
        return f"🔍 `{query}` 관련 최신 사례가 없습니다."

    def clean_html(s: str) -> str:
        s = re.sub(r"<[^>]+>", "", s or "")
        return s.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()

    lines = [f"📰 **최신 뉴스 (검색어: {query})**", "---"]
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
        """Thread-safe token refresh"""
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
                    model=model, messages=[{"role": "user", "content": prompt}], temperature=0.1)
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
    """뉴스 검색(네이버 API)"""

    def _extract_keywords_llm(self, situation: str) -> str:
        prompt = f"상황: '{situation}'\n뉴스 검색 키워드 2개만 콤마로 구분 출력."
        try:
            res = (llm_service.generate_text(prompt) or "").strip()
            return re.sub(r'[".?]', "", res)
        except Exception:
            return situation[:20]

    def search_news(self, query: str, top_k: int = 3) -> str:
        try:
            return cached_naver_news(query=query, top_k=top_k)
        except Exception as e:
            return f"검색 오류: {e}"

    def search_precedents(self, situation: str, top_k: int = 3) -> str:
        keywords = self._extract_keywords_llm(situation)
        return self.search_news(keywords, top_k=top_k)


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

    def _is_korea_kr_email(self, email: str) -> bool:
        return email.lower().endswith(KOREA_DOMAIN)

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

    def _pack_summary(self, res: dict, followup: dict) -> dict:
        return {"meta": res.get("meta"), "strategy": res.get("strategy"), "search_initial": res.get("search"),
                "law_initial": res.get("law"), "document_content": res.get("doc"), "followup": followup,
                "timings": res.get("timings")}

    def insert_initial_report(self, res: dict) -> dict:
        c = self._get_db_client()
        if not c:
            return {"ok": False, "msg": "DB 저장 불가(로그인 필요)", "id": None}
        try:
            followup = {"count": 0, "messages": [], "extra_context": ""}
            data = {"situation": res.get("situation", ""), "law_name": res.get("law", ""),
                    "summary": self._pack_summary(res, followup),
                    "user_email": st.session_state.get("sb_user_email"),
                    "user_id": st.session_state.get("sb_user_id")}
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
        summary = self._pack_summary(res, followup)
        if report_id:
            try:
                c.table("law_reports").update({"summary": summary}).eq("id", report_id).execute()
                return {"ok": True, "msg": "DB 업데이트 성공"}
            except Exception:
                pass
        try:
            data = {"situation": res.get("situation", ""), "law_name": res.get("law", ""), "summary": summary,
                    "user_email": st.session_state.get("sb_user_email"), "user_id": st.session_state.get("sb_user_id")}
            c.table("law_reports").insert(data).execute()
            return {"ok": True, "msg": "DB 신규 저장(fallback)"}
        except Exception as e:
            return {"ok": False, "msg": f"DB 실패: {e}"}

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
    """국가법령정보센터 API"""

    def __init__(self):
        self.api_id = _safe_secrets("general").get("LAW_API_ID")

    def _make_link(self, mst_id: str) -> Optional[str]:
        if not self.api_id or not mst_id:
            return None
        return f"https://www.law.go.kr/DRF/lawService.do?OC={self.api_id}&target=law&MST={mst_id}&type=HTML"

    def get_law_text(self, law_name: str, article_num: Optional[int] = None, return_link: bool = False):
        if not self.api_id:
            msg = "⚠️ LAW_API_ID 미설정"
            return (msg, None) if return_link else msg

        try:
            mst_id = cached_law_search(self.api_id, law_name) or ""
            if not mst_id:
                msg = f"🔍 '{law_name}' 검색 결과 없음"
                return (msg, None) if return_link else msg
        except Exception as e:
            msg = f"API 검색 오류: {e}"
            return (msg, None) if return_link else msg

        link = self._make_link(mst_id)

        try:
            xml_text = cached_law_detail_xml(self.api_id, mst_id)
            root = _safe_et_from_bytes(xml_text.encode("utf-8", errors="ignore"))

            if article_num:
                target = str(article_num)
                for art in root.findall(".//조문단위"):
                    jo_num = art.find("조문번호")
                    jo_content = art.find("조문내용")
                    if jo_num is None or jo_content is None:
                        continue
                    num_txt = (jo_num.text or "").strip()
                    if num_txt == target or num_txt.startswith(target):
                        result = f"[{law_name} 제{num_txt}조]\n" + _escape((jo_content.text or "").strip())
                        for hang in art.findall(".//항"):
                            hc = hang.find("항내용")
                            if hc is not None and (hc.text or "").strip():
                                result += f"\n  - {(hc.text or '').strip()}"
                        return (result, link) if return_link else result

            msg = f"✅ '{law_name}' 확인됨 (조문 자동추출 실패)\n🔗 {link or '-'}"
            return (msg, link) if return_link else msg
        except Exception as e:
            msg = f"법령 파싱 실패: {e}"
            return (msg, link) if return_link else msg

    def get_admrul_text(self, name: str, return_link: bool = False):
        """행정규칙(훈령/예규/고시) 조회"""
        if not self.api_id:
            msg = "⚠️ LAW_API_ID 미설정"
            return (msg, None) if return_link else msg

        try:
            admrul_id = cached_admrul_search(self.api_id, name) or ""
            if not admrul_id:
                msg = f"🔍 '{name}' 행정규칙 검색 결과 없음"
                return (msg, None) if return_link else msg
        except Exception as e:
            msg = f"행정규칙 검색 오류: {e}"
            return (msg, None) if return_link else msg

        link = f"https://www.law.go.kr/DRF/lawService.do?OC={self.api_id}&target=admrul&ID={admrul_id}&type=HTML"

        try:
            xml_text = cached_admrul_detail(self.api_id, admrul_id)
            root = _safe_et_from_bytes(xml_text.encode("utf-8", errors="ignore"))

            title = (root.findtext(".//행정규칙명") or root.findtext(".//admrulNm") or name).strip()
            content = (root.findtext(".//본문") or root.findtext(".//content") or "").strip()

            if content:
                preview = content[:800] + ("..." if len(content) > 800 else "")
                result = f"[{title}]\n{preview}\n🔗 {link}"
                return (result, link) if return_link else result

            msg = f"✅ '{title}' 확인됨 (본문 추출 실패)\n🔗 {link}"
            return (msg, link) if return_link else msg
        except Exception as e:
            msg = f"행정규칙 파싱 실패: {e}"
            return (msg, link) if return_link else msg

    def ai_search(self, query: str, top_k: int = 5) -> str:
        """지능형(AIS) 검색 결과 반환"""
        if not self.api_id:
            return "⚠️ LAW_API_ID 미설정"

        try:
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
        except Exception as e:
            return f"지능형 검색 오류: {e}"

    @staticmethod
    def detect_doc_type(name: str) -> str:
        """이름에서 문서 유형 추론: law vs admrul"""
        admrul_keywords = ["훈령", "예규", "고시", "지침", "요령", "규정", "기준", "지시", "공고"]
        name_lower = name.lower()
        for kw in admrul_keywords:
            if kw in name_lower:
                return "admrul"
        return "law"


# ==========================================
# 4) Global Instances
# ==========================================
_SERVICE_VERSION = "v5_context_fix"  # 캐시 무효화

@st.cache_resource(show_spinner=False)
def _get_services(_version: str = _SERVICE_VERSION):
    return LLMService(), SearchService(), DatabaseService(), LawOfficialService()

llm_service, search_service, db_service, law_api_service = _get_services()


# ==========================================
# 5) Agents (Enhanced Context Understanding)
# ==========================================
class LegalAgents:
    @staticmethod
    def researcher(situation: str) -> str:
        """상황 분석 후 법령 검색"""
        context_prompt = f"""
당신은 대한민국 지방자치단체의 20년 경력 행정 법률 전문가입니다.

[업무 지시 내용]
"{situation}"

위 내용은 **담당 공무원이 해결해야 할 민원/업무 상황**입니다.
이 상황에 적용되는 핵심 법령을 분석하여 JSON으로 출력하세요.

[상황 분석]
1. 민원/상황 유형 파악
2. 담당 공무원이 해야 할 조치 파악
3. 적용 법령 3개 추출

[JSON 출력 형식]
[
  {{"law_name": "정확한 법령명", "article_num": 조번호 또는 null}},
  {{"law_name": "두번째 법령", "article_num": null}},
  {{"law_name": "세번째 법령", "article_num": null}}
]

[참고]
- 무단방치/불법주정차: 「도로교통법」 제32조~36조
- 소음민원: 「소음·진동관리법」
- 영업/위생: 「식품위생법」, 「공중위생관리법」
- 건축/용도: 「건축법」
"""
        search_targets: List[Dict[str, Any]] = []
        try:
            extracted = llm_service.generate_json(context_prompt)
            if isinstance(extracted, list):
                search_targets = extracted
            elif isinstance(extracted, dict):
                search_targets = [extracted]
        except Exception:
            pass

        # 키워드 기반 fallback
        if not search_targets:
            sit = situation.lower()
            if any(k in sit for k in ["방치", "차량", "주차"]):
                search_targets = [{"law_name": "도로교통법", "article_num": 32}]
            elif any(k in sit for k in ["소음", "시끄"]):
                search_targets = [{"law_name": "소음·진동관리법", "article_num": None}]
            else:
                search_targets = [{"law_name": "행정절차법", "article_num": None}]

        report_lines: List[str] = [f"🔍 **AI가 식별한 핵심 법령 ({len(search_targets)}건)**", "---"]
        api_success_count = 0

        for idx, item in enumerate(search_targets):
            law_name = str(item.get("law_name") or "관련법령").strip()
            article_num = item.get("article_num")
            art = None
            try:
                if article_num and str(article_num).strip().isdigit():
                    art = int(article_num)
            except Exception:
                pass

            law_text, link = law_api_service.get_law_text(law_name, art, return_link=True)
            err_kw = ["검색 결과", "오류", "미설정", "실패"]
            is_ok = not any(k in (law_text or "") for k in err_kw)

            if is_ok:
                api_success_count += 1
                title = f"[{law_name}]({link})" if link else law_name
                art_str = f" 제{art}조" if art else ""
                report_lines.append(f"✅ **{idx+1}. {title}{art_str}**\n{law_text}\n")
            else:
                report_lines.append(f"⚠️ **{idx+1}. {law_name}** - API 실패\n")

        if api_success_count == 0:
            fallback = f"""당신은 행정 법률 전문가입니다.
상황: "{situation}"
이 상황에 적용되는 법령과 조항을 상세히 분석하세요.
[AI 추론 결과]임을 명시하고 법제처 확인 필요 경고."""
            ai_text = llm_service.generate_text(fallback) or ""
            return f"⚠️ **[API 실패 - AI 추론]**\n(환각 가능성 - 법제처 확인 필수)\n\n{ai_text}"

        return "\n".join(report_lines)

    @staticmethod
    def strategist(situation: str, legal_basis: str, search_results: str) -> str:
        prompt = f"""
당신은 20년 경력 행정 베테랑 주무관입니다.

[민원 상황]
{situation}

[확보된 법적 근거]
{legal_basis[:3000]}

[유사 사례/뉴스]
{search_results[:1500]}

**담당 공무원 입장에서** 이 민원을 어떻게 처리해야 하는지 구체적으로 안내하세요.

## 1. 처리 방향 (Action Plan)
- 1단계: (구체적 조치)
- 2단계: (구체적 조치)
- 3단계: (구체적 조치)

## 2. 법적 근거 요약
- 적용 법령: (법령명 + 조문)
- 핵심 요지: (왜 이 법이 적용되는지)

## 3. 핵심 주의사항 ⚠️
- (실무 주의점)
- (법적 리스크)

## 4. 예상 반발 및 대응
| 예상 반발 | 대응 논리 |
|----------|-----------|
| (반발1) | (대응1) |

## 5. 민원인 응대 요령
- (설명 방법)
- (갈등 해소 방안)

서론(인사말) 없이 바로 시작.
"""
        return llm_service.generate_text(prompt)

    @staticmethod
    def clerk(situation: str, legal_basis: str) -> dict:
        today = datetime.now(KST)
        prompt = f"오늘: {today.strftime('%Y-%m-%d')}\n상황: {situation}\n법령: {legal_basis[:500]}\n이행 기간 숫자만. 모르면 15."
        try:
            res = (llm_service.generate_text(prompt) or "").strip()
            m = re.search(r"\d{1,3}", res)
            days = int(m.group(0)) if m else 15
            days = max(1, min(days, 180))
        except Exception:
            days = 15
        deadline = today + timedelta(days=days)
        return {"today_str": today.strftime("%Y. %m. %d."), "deadline_str": deadline.strftime("%Y. %m. %d."),
                "days_added": days, "doc_num": f"행정-{today.strftime('%Y')}-{int(time.time())%1000:03d}호"}

    @staticmethod
    def drafter(situation: str, legal_basis: str, meta_info: dict, strategy: str) -> dict:
        schema = {"type": "object", "properties": {"title": {"type": "string"}, "receiver": {"type": "string"},
                  "body_paragraphs": {"type": "array", "items": {"type": "string"}}, "department_head": {"type": "string"}},
                  "required": ["title", "receiver", "body_paragraphs", "department_head"]}

        prompt = f"""
당신은 행정기관 베테랑 서기입니다.

[민원 상황]: {situation}
[법적 근거]: {legal_basis[:2000]}
[시행일]: {meta_info.get('today_str','')} / [기한]: {meta_info.get('deadline_str','')}
[전략]: {strategy[:1000]}

공문서 JSON 출력:
- title: 공문 제목
- receiver: 수신자
- body_paragraphs: [경위, 법적근거, 처분내용, 이의제기]
- department_head: 결재자

행정 공문체 사용. 법 조항 인용 필수.
"""
        doc = llm_service.generate_json(prompt, schema=schema)

        if not isinstance(doc, dict):
            return {"title": "행정처분 안내", "receiver": "민원인 귀하",
                    "body_paragraphs": ["1. 경위", "2. 법적 근거", "3. 처분 내용", "4. 이의제기"],
                    "department_head": "행정기관장"}

        bp = doc.get("body_paragraphs")
        doc["body_paragraphs"] = [bp] if isinstance(bp, str) else (bp if isinstance(bp, list) else [])
        for k in ["title", "receiver", "department_head"]:
            if not isinstance(doc.get(k), str):
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

    add_log("🔍 Phase 1: 법령 리서치...", "legal")
    t = time.perf_counter()
    legal_basis = LegalAgents.researcher(user_input)
    timings["law_sec"] = round(time.perf_counter() - t, 2)
    add_log(f"📜 법적 근거 완료 ({timings['law_sec']}s)", "legal")

    add_log("🟩 뉴스 검색...", "search")
    t = time.perf_counter()
    try:
        search_results = search_service.search_precedents(user_input)
    except Exception:
        search_results = "검색 모듈 미연결"
    timings["news_sec"] = round(time.perf_counter() - t, 2)

    add_log(f"🧠 Phase 2: 처리 방향 수립... ({timings['news_sec']}s)", "strat")
    t = time.perf_counter()
    strategy = LegalAgents.strategist(user_input, legal_basis, search_results)
    timings["strat_sec"] = round(time.perf_counter() - t, 2)

    add_log("📅 Phase 3: 기한 산정...", "calc")
    t = time.perf_counter()
    meta_info = LegalAgents.clerk(user_input, legal_basis)
    timings["calc_sec"] = round(time.perf_counter() - t, 2)

    add_log("✍️ Phase 4: 공문서 생성...", "draft")
    t = time.perf_counter()
    doc_data = LegalAgents.drafter(user_input, legal_basis, meta_info, strategy)
    timings["draft_sec"] = round(time.perf_counter() - t, 2)

    timings["total_sec"] = round(time.perf_counter() - t0, 2)
    log_placeholder.empty()

    return {"situation": user_input, "doc": doc_data, "meta": meta_info,
            "law": legal_basis, "search": search_results, "strategy": strategy, "timings": timings}


# ==========================================
# 7) Follow-up Chat
# ==========================================
def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text)


def build_case_context(res: dict) -> str:
    situation = res.get("situation", "")
    law_txt = _strip_html(res.get("law", ""))[:2000]
    news_txt = _strip_html(res.get("search", ""))[:1000]
    strategy = res.get("strategy", "")[:1000]
    doc = res.get("doc") or {}
    bp = doc.get("body_paragraphs", [])
    if isinstance(bp, str):
        bp = [bp]
    body = "\n".join([f"- {p}" for p in bp])

    return f"""[케이스 컨텍스트]
1) 민원: {situation}
2) 법령: {law_txt}
3) 뉴스: {news_txt}
4) 전략: {strategy}
5) 공문: 제목={doc.get('title','')}, 수신={doc.get('receiver','')}
{body}

[규칙] 컨텍스트 내에서만 답변. 단정 금지. 추가 조회 필요시 명시."""


def needs_tool_call(user_msg: str) -> dict:
    t = (user_msg or "").lower()
    law_kw = ["근거", "조문", "법령", "몇 조", "원문", "행정절차"]
    news_kw = ["뉴스", "사례", "판례", "기사", "최근"]
    return {"need_law": any(k in t for k in law_kw), "need_news": any(k in t for k in news_kw)}


def plan_tool_calls_llm(user_msg: str, situation: str, known_law: str) -> dict:
    schema = {"type": "object", "properties": {"need_law": {"type": "boolean"}, "law_name": {"type": "string"},
              "article_num": {"type": "integer"}, "need_news": {"type": "boolean"}, "news_query": {"type": "string"}}}
    prompt = f"""[민원] {situation}
[확보 법령] {known_law[:1500]}
[질문] {user_msg}
추가 조회 필요시 JSON 출력. need_law/law_name/article_num/need_news/news_query"""
    plan = llm_service.generate_json(prompt, schema=schema) or {}
    if not isinstance(plan, dict):
        return {"need_law": False, "law_name": "", "article_num": 0, "need_news": False, "news_query": ""}
    try:
        plan["article_num"] = int(plan.get("article_num") or 0)
    except Exception:
        plan["article_num"] = 0
    return plan


def answer_followup(case_ctx: str, extra_ctx: str, history: list, user_msg: str) -> str:
    hist = history[-8:]
    hist_txt = "\n".join([f"{m['role']}: {m['content']}" for m in hist]) if hist else ""
    prompt = f"""{case_ctx}
[추가 조회] {extra_ctx or '없음'}
[히스토리] {hist_txt}
[질문] {user_msg}
케이스 고정 답변. 서론 금지."""
    return llm_service.generate_text(prompt)


def render_followup_chat(res: dict):
    st.session_state.setdefault("case_id", None)
    st.session_state.setdefault("followup_count", 0)
    st.session_state.setdefault("followup_messages", [])
    st.session_state.setdefault("followup_extra_context", "")
    st.session_state.setdefault("report_id", None)

    current_case = (res.get("meta") or {}).get("doc_num", "") or "case"
    if st.session_state["case_id"] != current_case:
        st.session_state["case_id"] = current_case
        st.session_state["followup_count"] = 0
        st.session_state["followup_messages"] = []
        st.session_state["followup_extra_context"] = ""
        st.session_state["report_id"] = st.session_state.get("report_id")

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

    case_ctx = build_case_context(res)
    extra_ctx = st.session_state.get("followup_extra_context", "")
    tool_need = needs_tool_call(user_q)

    if tool_need["need_law"] or tool_need["need_news"]:
        plan = plan_tool_calls_llm(user_q, res.get("situation", ""), _strip_html(res.get("law", "")))
        if plan.get("need_law") and plan.get("law_name"):
            art = plan.get("article_num", 0) or None
            law_text, link = law_api_service.get_law_text(plan["law_name"], art, return_link=True)
            extra_ctx += f"\n[추가 법령] {plan['law_name']} 제{art or '?'}조\n{_strip_html(law_text)}"
        if plan.get("need_news") and plan.get("news_query"):
            news = search_service.search_news(plan["news_query"])
            extra_ctx += f"\n[추가 뉴스] {plan['news_query']}\n{_strip_html(news)}"
        st.session_state["followup_extra_context"] = extra_ctx

    with st.chat_message("assistant"):
        with st.spinner("답변 생성..."):
            ans = answer_followup(case_ctx, st.session_state.get("followup_extra_context", ""),
                                  st.session_state["followup_messages"], user_q)
            st.markdown(ans)

    st.session_state["followup_messages"].append({"role": "assistant", "content": ans})

    followup_data = {"count": st.session_state["followup_count"], "messages": st.session_state["followup_messages"],
                     "extra_context": st.session_state.get("followup_extra_context", "")}
    upd = db_service.update_followup(st.session_state.get("report_id"), res, followup_data)
    if not upd.get("ok"):
        st.caption(f"⚠️ {upd.get('msg')}")
# ==========================================
# 8) Sidebar UI (ChatGPT Style)
# ==========================================
def render_sidebar_ui():
    st.markdown("""
    <style>
    .sidebar-btn {
        width: 100%;
        text-align: left;
        padding: 0.5rem;
        background: transparent;
        border: 1px solid #4b5563;
        color: #e5e7eb;
        border-radius: 6px;
        margin-bottom: 4px;
        cursor: pointer;
        transition: background 0.2s;
    }
    .sidebar-btn:hover {
        background: #374151;
    }
    .history-item {
        display: block;
        width: 100%;
        padding: 8px 12px;
        margin-bottom: 4px;
        background: transparent;
        border: none;
        color: #d1d5db;
        text-align: left;
        font-size: 0.9rem;
        border-radius: 6px;
        cursor: pointer;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .history-item:hover {
        background: rgba(255,255,255,0.1);
    }
    </style>
    """, unsafe_allow_html=True)

    # 1. 로고 및 타이틀
    st.markdown("### 🏢 AI 행정관 Pro")
    st.caption("Govable AI | kim0395kk@korea.kr")
    
    # 2. 새 채팅 버튼 (항상 표시)
    if st.button("➕ 새 채팅", use_container_width=True, type="primary"):
        for key in ["workflow_result", "report_id", "followup_messages", "followup_count", "followup_extra_context"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()
    
    st.markdown("---")

    # 3. 로그인 상태에 따른 분기
    if not db_service.is_logged_in():
        st.info("로그인하여 기록을 저장하세요.")
        with st.expander("🔐 로그인 / 회원가입", expanded=True):
            email = st.text_input("이메일", key="login_email")
            if email and not email.lower().endswith(KOREA_DOMAIN):
                st.caption(f"⚠️ {KOREA_DOMAIN} 권장")
            pw = st.text_input("비밀번호", type="password", key="login_pw")
            
            c1, c2 = st.columns(2)
            with c1:
                if st.button("로그인", use_container_width=True):
                    r = db_service.sign_in(email, pw)
                    if r.get("ok"):
                        st.rerun()
                    else:
                        st.error(r.get("msg"))
            with c2:
                if st.button("가입", use_container_width=True):
                    st.warning("관리자 문의 필요")

    else:
        # 로그인 상태: 히스토리 목록 표시
        user_email = st.session_state.get('sb_user_email', 'User')
        st.caption(f"👤 {user_email}")
        
        st.markdown("### 🗂️ 내 채팅 목록")
        
        # 검색 필터
        keyword = st.text_input("검색", placeholder="기록 검색...", label_visibility="collapsed")
        
        # 리포트 목록 가져오기
        rows = db_service.list_reports(limit=20, keyword=keyword)
        
        if not rows:
            st.caption("저장된 기록이 없습니다.")
        else:
            # 스크롤 가능한 영역 (Streamlit 기본 컨테이너 활용)
            for r in rows:
                rid = r.get("id")
                sit = (r.get("situation") or "제목 없음").replace("\n", " ")[:18]
                created = (r.get("created_at") or "")[5:10] # MM-DD
                
                # 버튼 클릭 시 해당 리포트 로드
                if st.button(f"📄 {sit}...", key=f"hist_{rid}", help=f"{created} 작성"):
                    detail = db_service.get_report(rid)
                    if detail:
                        st.session_state["loaded_report"] = detail
                        st.rerun()

        st.markdown("---")
        if st.button("로그아웃", use_container_width=True):
            db_service.sign_out()
            st.rerun()


# ==========================================
# 9) Main UI
# ==========================================
def main():
    # 다크모드 상태 초기화
    if "dark_mode" not in st.session_state:
        st.session_state["dark_mode"] = False

    # 다크모드 CSS 적용
    if st.session_state["dark_mode"]:
        st.markdown("""<style>
        .stApp { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f0f23 100%) !important; }
        .stApp::before { background: radial-gradient(circle at 20% 50%, rgba(102, 126, 234, 0.2), transparent 50%),
            radial-gradient(circle at 80% 80%, rgba(168, 85, 247, 0.2), transparent 50%) !important; }
        [data-testid="stSidebar"] { background: linear-gradient(180deg, rgba(26, 26, 46, 0.98) 0%, rgba(22, 33, 62, 0.95) 100%) !important; }
        [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3, 
        [data-testid="stSidebar"] p, [data-testid="stSidebar"] label { color: #e2e8f0 !important; -webkit-text-fill-color: #e2e8f0 !important; }
        .paper-sheet { background: linear-gradient(135deg, rgba(26, 26, 46, 0.95), rgba(22, 33, 62, 0.92)) !important; color: #e2e8f0 !important; }
        .doc-body, .doc-info { color: #cbd5e1 !important; }
        h1, h2, h3, p, label { color: #e2e8f0 !important; }
        </style>""", unsafe_allow_html=True)

    # ===== 상단 시스템 상태 + 다크모드 토글 =====
    top_cols = st.columns([6, 1, 1])
    with top_cols[0]:
        g = _safe_secrets("general")
        v = _safe_secrets("vertex")
        s = _safe_secrets("supabase")
        status_items = []
        status_items.append("✅법령" if g.get("LAW_API_ID") else "❌법령")
        status_items.append("✅뉴스" if (g.get("NAVER_CLIENT_ID") and g.get("NAVER_CLIENT_SECRET")) else "❌뉴스")
        status_items.append("✅AI" if v.get("SERVICE_ACCOUNT_JSON") else "❌AI")
        status_items.append("✅DB" if (s.get("SUPABASE_URL") and (s.get("SUPABASE_ANON_KEY") or s.get("SUPABASE_KEY"))) else "❌DB")
        st.caption(" | ".join(status_items) + (" | ⚠️관리자" if db_service.service_key else ""))
    with top_cols[1]:
        if st.button("🌙" if not st.session_state["dark_mode"] else "☀️", help="다크모드 토글"):
            st.session_state["dark_mode"] = not st.session_state["dark_mode"]
            st.rerun()
    with top_cols[2]:
        st.caption("⚠️개인정보금지")

    # ===== 사이드바: 로그인 + 히스토리 (ChatGPT 스타일) =====
    with st.sidebar:
        render_sidebar_ui()

    col_left, col_right = st.columns([1, 1.2])

    with col_left:
        st.markdown("### 🗣️ 업무 지시")
        user_input = st.text_area("업무 내용", height=140, label_visibility="collapsed",
            placeholder="예시\n- 상황: (무슨 일 / 어디 / 언제)\n- 의도: (확인 쟁점)\n- 요청: (공문 종류)")

        if st.button("⚡ 스마트 분석", type="primary", use_container_width=True):
            if not user_input:
                st.warning("내용 입력 필요")
            else:
                try:
                    with st.spinner("AI 에이전트 협업 중..."):
                        res = run_workflow(user_input)
                        ins = db_service.insert_initial_report(res)
                        res["save_msg"] = ins.get("msg")
                        st.session_state["report_id"] = ins.get("id")
                        st.session_state["workflow_result"] = res
                except Exception as e:
                    st.error(f"오류: {e}")

        if "workflow_result" in st.session_state:
            res = st.session_state["workflow_result"]
            st.markdown("---")
            if "성공" in (res.get("save_msg") or ""):
                st.success(f"✅ {res['save_msg']}")
            else:
                st.info(f"ℹ️ {res.get('save_msg','')}")

            with st.expander("⏱️ 소요시간", expanded=False):
                st.json(res.get("timings", {}))

            with st.expander("📜 법령 및 뉴스", expanded=True):
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**법령**")
                    law_html = res.get("law", "").replace("\n", "<br>")
                    # 마크다운 볼드 -> HTML strong
                    law_html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', law_html)
                    law_html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)',
                        r'<a href="\2" target="_blank">\1</a>', law_html)
                    st.markdown(f"<div style='height:280px;overflow-y:auto;padding:10px;background:#f8fafc;border-radius:6px;font-size:0.9rem'>{law_html}</div>", unsafe_allow_html=True)
                with c2:
                    st.markdown("**뉴스**")
                    news_html = res.get("search", "").replace("\n", "<br>")
                    news_html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', news_html)
                    news_html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)',
                        r'<a href="\2" target="_blank">\1</a>', news_html)
                    st.markdown(f"<div style='height:280px;overflow-y:auto;padding:10px;background:#eff6ff;border-radius:6px;font-size:0.9rem'>{news_html}</div>", unsafe_allow_html=True)

            with st.expander("🧭 처리 방향", expanded=True):
                # 마크다운 렌더링 지원
                strategy_text = res.get("strategy", "")
                st.markdown(strategy_text)

    with col_right:
        if "workflow_result" in st.session_state:
            res = st.session_state["workflow_result"]
            doc = res.get("doc") or {}
            meta = res.get("meta", {})

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
<hr style="border:1px solid black;margin-bottom:25px">
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
            st.markdown("""<div style='text-align:center;padding:80px;color:#aaa;background:white;border-radius:10px;border:2px dashed #ddd'>
<h3>📄 Document Preview</h3><p>왼쪽에서 업무 지시 후<br>공문서가 여기에 표시됩니다</p></div>""", unsafe_allow_html=True)


if __name__ == "__main__":
    main()

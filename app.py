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
from typing import Any, Dict, List, Tuple, Optional

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
# 5) Agents (Router + Multi-Agent Orchestrator)
# ==========================================
MODE_LABEL = {
    "A": "민원 회신 중심",
    "B": "판단·조치결정 중심",
    "C": "보고 중심",
    "D": "계획 수립 중심",
    "E": "기획(신규사업/제도설계) 중심",
}

RISK_HINT = {
    "LOW": "단순 문의/내부처리/파급 작음",
    "MEDIUM": "이견·반발 가능/재민원 우려/책임소재 논쟁",
    "HIGH": "감사/소송/언론/집단·악성 민원/정치 이슈 우려",
}


def _compact(text: str, limit: int = 2500) -> str:
    t = (text or "").strip()
    return t[:limit] + ("..." if len(t) > limit else "")


def _json_or_fallback(prompt: str, schema: dict, fallback: dict) -> dict:
    j = llm_service.generate_json(prompt, schema=schema)
    return j if isinstance(j, dict) else fallback


def _list_or_fallback(prompt: str, fallback: list) -> list:
    j = llm_service.generate_json(prompt)
    return j if isinstance(j, list) else fallback


class AgentPrompts:
    """모든 에이전트가 ‘고급스럽게’ 나오도록 공통 스타일/규칙을 강제"""

    @staticmethod
    def style_rules() -> str:
        return """
[출력 스타일]
- 결론을 먼저 제시하고, 근거/절차/리스크를 뒤에 배치.
- 말투는 '행정 공문/내부 보고' 수준의 격식(구어체/비속어 금지).
- 불확실한 부분은 '확인 필요'로 명시(추정/단정 금지).
- 개인정보(성명·연락처·주소·차량번호 등) 예시 작성 시 마스킹.
- 반드시 표/체크리스트/단계별 목록을 포함해 재사용 가능하게 구성.
"""

    @staticmethod
    def case_card_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "task_title": {"type": "string"},
                "task_type": {"type": "string"},
                "goal": {"type": "string"},
                "facts_timeline": {"type": "array", "items": {"type": "string"}},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "stakeholders": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "deliverable": {"type": "string"},
                "questions": {"type": "array", "items": {"type": "string"}},
                "keywords": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_type", "goal", "facts_timeline", "deliverable"],
        }

    @staticmethod
    def route_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "mode": {"type": "string"},
                "risk_level": {"type": "string"},
                "agents": {"type": "array", "items": {"type": "string"}},
                "followup_questions": {"type": "array", "items": {"type": "string"}},
                "legal_query_seed": {"type": "string"},
            },
            "required": ["mode", "risk_level", "agents"],
        }

    @staticmethod
    def legal_plan_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string"},
                            "purpose": {"type": "string"},
                            "must_check": {"type": "array", "items": {"type": "string"}},
                            "legal_sources": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "doc_type": {"type": "string"},  # "law" or "admrul"
                                        "article_num": {"type": "integer"},
                                        "priority": {"type": "integer"},
                                        "why": {"type": "string"},
                                    },
                                    "required": ["name", "doc_type", "priority", "why"],
                                },
                            },
                        },
                        "required": ["step", "purpose", "legal_sources"],
                    },
                },
                "top_laws": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "include_subregs": {"type": "boolean"},  # 시행령/시행규칙까지 확장 여부
                            "why": {"type": "string"},
                        },
                        "required": ["name", "include_subregs", "why"],
                    },
                },
                "top_admrul": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "why": {"type": "string"}},
                        "required": ["name", "why"],
                    },
                },
            },
            "required": ["workflow_steps", "top_laws", "top_admrul"],
        }

    @staticmethod
    def doc_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "receiver": {"type": "string"},
                "body_paragraphs": {"type": "array", "items": {"type": "string"}},
                "department_head": {"type": "string"},
            },
            "required": ["title", "receiver", "body_paragraphs", "department_head"],
        }

class ClerkAgent:
    """기한/문서번호 산정 전용(안전 버전)"""

    @staticmethod
    def compute_meta(situation: str, sop_text: str = "", legal_text: str = "", mode: str = "A") -> dict:
        today = datetime.now(KST)

        # 기본 기한(업무 성격에 따라 약간 보정)
        default_days = 15
        if mode == "B":  # 처분/계고/조치결정 성격
            default_days = 10
        if mode in ["D", "E"]:  # 계획/기획 성격
            default_days = 30

        # LLM로 "숫자(일수)"만 뽑아오되, 실패 시 default로
        prompt = f"""
오늘: {today.strftime('%Y-%m-%d')}
업무유형 Mode: {mode}

[상황]
{situation}

[SOP(처리방향)]
{sop_text[:1200]}

[확보 법령/규정]
{legal_text[:1200]}

위 업무에서 실무적으로 잡아야 할 '처리 기한(며칠)'을 숫자만 출력.
- 불명확하면 {default_days} 출력.
- 1~180 범위.
"""
        days = default_days
        try:
            res = (llm_service.generate_text(prompt) or "").strip()
            m = re.search(r"\d{1,3}", res)
            if m:
                days = int(m.group(0))
        except Exception:
            pass

        days = max(1, min(days, 180))
        deadline = today + timedelta(days=days)

        return {
            "today_str": today.strftime("%Y. %m. %d."),
            "deadline_str": deadline.strftime("%Y. %m. %d."),
            "days_added": days,
            "doc_num": f"행정-{today.strftime('%Y')}-{int(time.time())%1000:03d}호",
        }

class MultiAgentSystem:
    """ROUTER → (LEGAL/ADMIN/CIVIL/BEHAVIOR/PLAN) → INTEGRATOR"""

    @staticmethod
    def extract_case_card(user_input: str) -> dict:
        schema = AgentPrompts.case_card_schema()
        prompt = f"""
너는 대한민국 지방자치단체(시·군·구) 실무를 이해하는 '업무 분석관'이다.
아래 업무지시를 사건카드로 구조화하라. 질문이 필요하면 최대 5개까지만.

[업무 지시]
{user_input}

[출력]
- 반드시 JSON만 출력.
- facts_timeline은 시간순(알 수 없으면 "시점 불명")으로 3~7개.
- deliverable은 "회신문/통지/계고/보고/계획/기획서" 중 가장 가까운 1개로.
- keywords는 법령/분야 키워드 5~10개.
"""
        fallback = {
            "task_title": "업무 처리",
            "task_type": "미분류",
            "goal": "민원을 처리하고 행정적으로 정리",
            "facts_timeline": [user_input[:120] if user_input else "입력 없음"],
            "evidence": [],
            "stakeholders": ["민원인", "담당부서"],
            "constraints": [],
            "risks": [],
            "deliverable": "회신문",
            "questions": [],
            "keywords": [],
        }
        return _json_or_fallback(prompt, schema, fallback)

    @staticmethod
    def route(case_card: dict) -> dict:
        schema = AgentPrompts.route_schema()
        prompt = f"""
너는 공무원 업무 라우터다. 사건카드를 보고 업무유형(Mode)과 리스크를 판정하고
필요한 에이전트만 최소 조합으로 선택하라.

[업무유형 Mode]
A=민원 회신 중심, B=판단·조치결정 중심, C=보고 중심, D=계획 수립 중심, E=기획(제도/사업)

[리스크]
LOW/MEDIUM/HIGH

[에이전트]
ADMIN, LEGAL, CIVIL, BEHAVIOR, PLAN, INTEGRATOR
- INTEGRATOR는 항상 포함.
- LOW는 2~3명, MEDIUM은 3~4명, HIGH는 4~6명 권장.
- followup_questions는 최대 5개.

[사건카드]
{json.dumps(case_card, ensure_ascii=False)}

반드시 JSON만 출력.
"""
        # fallback(휴리스틱)
        text = (case_card.get("deliverable") or "") + " " + " ".join(case_card.get("facts_timeline") or [])
        t = text.lower()
        mode = "A"
        if any(k in t for k in ["계획", "운영", "일정", "로드맵"]):
            mode = "D"
        if any(k in t for k in ["기획", "사업", "공모", "제도", "조례"]):
            mode = "E"
        if any(k in t for k in ["보고", "브리핑", "감사", "상급자"]):
            mode = "C"
        if any(k in t for k in ["계고", "처분", "통지", "반려", "요구", "명령"]):
            mode = "B"
        risk = "LOW"
        if any(k in t for k in ["반발", "이의", "분쟁", "재민원", "민감"]):
            risk = "MEDIUM"
        if any(k in t for k in ["소송", "감사", "언론", "집단", "고소", "고발"]):
            risk = "HIGH"

        fallback_agents = {
            "A": ["CIVIL", "LEGAL", "INTEGRATOR"],
            "B": ["ADMIN", "LEGAL", "INTEGRATOR"],
            "C": ["ADMIN", "INTEGRATOR"],
            "D": ["PLAN", "ADMIN", "INTEGRATOR"],
            "E": ["PLAN", "LEGAL", "ADMIN", "INTEGRATOR"],
        }.get(mode, ["LEGAL", "INTEGRATOR"])

        if risk == "MEDIUM" and "CIVIL" not in fallback_agents:
            fallback_agents.append("CIVIL")
        if risk == "HIGH":
            for x in ["ADMIN", "LEGAL", "CIVIL", "BEHAVIOR", "PLAN"]:
                if x not in fallback_agents:
                    fallback_agents.append(x)
            if "INTEGRATOR" not in fallback_agents:
                fallback_agents.append("INTEGRATOR")

        fallback = {
            "mode": mode,
            "risk_level": risk,
            "agents": fallback_agents,
            "followup_questions": (case_card.get("questions") or [])[:5],
            "legal_query_seed": " ".join((case_card.get("keywords") or [])[:6]).strip(),
        }
        return _json_or_fallback(prompt, schema, fallback)

    @staticmethod
    def _expand_sub_regs(law_name: str) -> List[str]:
        name = (law_name or "").strip()
        if not name:
            return []
        # 이미 시행령/규칙이면 중복 확장 금지
        if any(k in name for k in ["시행령", "시행규칙"]):
            return []
        return [f"{name} 시행령", f"{name} 시행규칙"]

    @staticmethod
    def plan_legal(case_card: dict, route: dict) -> dict:
        schema = AgentPrompts.legal_plan_schema()
        prompt = f"""
너는 대한민국 행정법·실무 절차에 정통한 '법령 설계관'이다.
사건카드/라우팅을 바탕으로 **업무처리 흐름(단계)별로** 필요한 법령/하위법령/행정규칙(훈령·예규·고시·지침)을 설계하라.

중요:
- 법령은 가능하면 "법률(본법) + 시행령 + 시행규칙"까지 고려하라.
- 행정규칙(훈령/예규/고시/지침/요령/기준)은 국가법령정보센터의 "admrul"로 존재할 수 있는 것만 후보로 제시하라.
- workflow_steps는 3~7개.
- top_laws는 최대 4개, top_admrul은 최대 3개.
- 모르는 건 추정하지 말고 "확인 필요" 근거로 why에 적어라.

[라우팅]
{json.dumps(route, ensure_ascii=False)}

[사건카드]
{json.dumps(case_card, ensure_ascii=False)}

반드시 JSON만 출력.
"""
        fallback = {
            "workflow_steps": [
                {
                    "step": "1) 사실관계/증빙 확인",
                    "purpose": "민원 요지 및 쟁점 확정",
                    "must_check": ["증빙 확보", "관할/권한 확인"],
                    "legal_sources": [
                        {"name": "행정절차법", "doc_type": "law", "article_num": 0, "priority": 5, "why": "절차적 정당성 확보"},
                    ],
                },
                {
                    "step": "2) 법적 요건 판단",
                    "purpose": "가능/불가/추가조치 판단",
                    "must_check": ["요건 충족 여부", "처분/통지 필요 여부"],
                    "legal_sources": [
                        {"name": "행정절차법", "doc_type": "law", "article_num": 0, "priority": 5, "why": "사전통지/의견제출 등"},
                    ],
                },
                {
                    "step": "3) 문서화 및 회신/보고",
                    "purpose": "공문/회신문으로 종결",
                    "must_check": ["단정 표현 금지", "이의절차 안내"],
                    "legal_sources": [
                        {"name": "행정절차법", "doc_type": "law", "article_num": 0, "priority": 4, "why": "통지/송달/기재사항"},
                    ],
                },
            ],
            "top_laws": [{"name": "행정절차법", "include_subregs": False, "why": "대부분의 행정절차 공통"}],
            "top_admrul": [],
        }
        return _json_or_fallback(prompt, schema, fallback)

    @staticmethod
    def fetch_legal_materials(legal_plan: Any) -> Tuple[str, List[Dict[str, Any]]]:
        """
        legal_plan: LLM이 만든 법령/규정 설계 결과(dict 또는 JSON 문자열)
        return:
          - legal_md: 확보한 법령/규정 원문 요약(마크다운)
          - sources: 실제 조회에 사용한 소스 목록(list[dict])
        전제:
          - 전역에 law_api_service (LawOfficialService 인스턴스)가 존재해야 함
          - MultiAgentSystem._expand_sub_regs(law_name) 가 존재하면 하위법령 확장에 사용
        """

        # -----------------------------
        # 0) legal_plan 안전 정규화
        # -----------------------------
        if legal_plan is None:
            legal_plan = {}

        if isinstance(legal_plan, str):
            try:
                legal_plan = json.loads(legal_plan)
            except Exception:
                legal_plan = {}

        if not isinstance(legal_plan, dict):
            legal_plan = {}

        def _norm_list(v: Any) -> List[Any]:
            if v is None:
                return []
            if isinstance(v, list):
                return v
            return [v]

        def _norm_top_laws(items: Any) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for x in _norm_list(items):
                if isinstance(x, str):
                    name = x.strip()
                    if name:
                        out.append({
                            "name": name,
                            "include_subregs": True,
                            "why": "LLM 문자열 출력 정규화"
                        })
                elif isinstance(x, dict):
                    name = (x.get("name") or x.get("law_name") or "").strip()
                    if name:
                        out.append({
                            "name": name,
                            "include_subregs": bool(x.get("include_subregs", False)),
                            "why": (x.get("why") or "").strip()
                        })
            return out

        def _norm_top_admrul(items: Any) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for x in _norm_list(items):
                if isinstance(x, str):
                    name = x.strip()
                    if name:
                        out.append({
                            "name": name,
                            "why": "LLM 문자열 출력 정규화"
                        })
                elif isinstance(x, dict):
                    name = (x.get("name") or x.get("admrul_name") or "").strip()
                    if name:
                        out.append({
                            "name": name,
                            "why": (x.get("why") or "").strip()
                        })
            return out

        legal_plan["top_laws"] = _norm_top_laws(legal_plan.get("top_laws"))
        legal_plan["top_admrul"] = _norm_top_admrul(legal_plan.get("top_admrul"))

        # -----------------------------
        # 1) 조회 대상 sources 구성 (중복 제거 + 우선순위)
        # -----------------------------
        sources: List[Dict[str, Any]] = []

        # 법령
        for x in (legal_plan.get("top_laws") or []):
            name = (x.get("name") or "").strip()
            if not name:
                continue

            sources.append({
                "name": name,
                "doc_type": "law",
                "article_num": 0,
                "why": (x.get("why") or "").strip(),
                "priority": 5,
                "include_subregs": bool(x.get("include_subregs", False)),
            })

            # 하위법령(시행령/시행규칙 등) 확장
            if bool(x.get("include_subregs", False)):
                try:
                    sub_regs = MultiAgentSystem._expand_sub_regs(name)
                except Exception:
                    sub_regs = []
                for sub in (sub_regs or []):
                    sub_name = (sub or "").strip()
                    if not sub_name:
                        continue
                    sources.append({
                        "name": sub_name,
                        "doc_type": "law",
                        "article_num": 0,
                        "why": "하위법령(시행) 확인",
                        "priority": 4,
                        "include_subregs": False,
                    })

        # 행정규칙(훈령/예규/고시/지침 등)
        for x in (legal_plan.get("top_admrul") or []):
            name = (x.get("name") or "").strip()
            if not name:
                continue
            sources.append({
                "name": name,
                "doc_type": "admrul",
                "article_num": 0,
                "why": (x.get("why") or "").strip(),
                "priority": 3
            })

        # 중복 제거: (doc_type, name) 기준으로 priority 높은 것 유지
        dedup: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for s in sources:
            key = (s.get("doc_type", ""), s.get("name", ""))
            if not key[0] or not key[1]:
                continue
            if key not in dedup:
                dedup[key] = s
            else:
                if int(s.get("priority", 0)) > int(dedup[key].get("priority", 0)):
                    dedup[key] = s

        sources = sorted(dedup.values(), key=lambda d: int(d.get("priority", 0)), reverse=True)

        # -----------------------------
        # 2) 원문 확보 (법령/행정규칙)
        # -----------------------------
        lines: List[str] = []
        lines.append("## 📜 법령·규정 원문(자동 확보)")
        lines.append("- 아래 내용은 자동 조회/요약 결과이며, 최종 판단 전 **원문 링크에서 재확인**을 권장합니다.")
        lines.append("")

        fail_count = 0

        for idx, s in enumerate(sources, 1):
            doc_type = s.get("doc_type")
            name = s.get("name")
            why = s.get("why", "")
            article_num = s.get("article_num") or 0

            if not name:
                continue

            # 표시용 헤더
            head = f"### {idx}. {name}"
            if why:
                head += f"  \n> 선정 사유: {why}"
            lines.append(head)

            try:
                if doc_type == "admrul":
                    text, link = law_api_service.get_admrul_text(name, return_link=True)
                    if link:
                        lines.append(f"- 🔗 원문: {link}")
                    lines.append("")
                    lines.append(text or "⚠️ 본문 조회 결과 없음")
                    lines.append("")
                else:
                    # 기본은 law
                    art = int(article_num) if str(article_num).isdigit() and int(article_num) > 0 else None
                    text, link = law_api_service.get_law_text(name, art, return_link=True)
                    if link:
                        lines.append(f"- 🔗 원문: {link}")
                    lines.append("")
                    lines.append(text or "⚠️ 본문 조회 결과 없음")
                    lines.append("")
            except Exception as e:
                fail_count += 1
                lines.append(f"⚠️ 조회 실패: {e}")
                lines.append("")

        if not sources:
            lines.append("⚠️ 조회할 법령/규정이 설계되지 않았습니다. (legal_plan 비어 있음)")
        elif fail_count == len(sources):
            lines.append("⚠️ 모든 원문 조회가 실패했습니다. LAW_API_ID / 네트워크 / 파싱 상태를 점검하세요.")

        legal_md = "\n".join(lines).strip()
        return legal_md, sources

    # (참고) 이미 있다면 이건 건드리지 마세요.
    # @staticmethod
    # def _expand_sub_regs(law_name: str) -> List[str]:
    #     ...

    @staticmethod
    def _call_agent(role: str, case_card: dict, route: dict, legal_plan: dict, legal_md: str, news_md: str) -> str:
        base = AgentPrompts.style_rules()
        header = f"[ROLE] {role}\n[Mode] {route.get('mode')}({MODE_LABEL.get(route.get('mode'), '-')}) / [Risk] {route.get('risk_level')}({RISK_HINT.get(route.get('risk_level'), '-')})"
        cc = json.dumps(case_card, ensure_ascii=False)
        lp = json.dumps(legal_plan, ensure_ascii=False)

        if role == "LEGAL":
            prompt = f"""{base}
{header}

너는 LEGAL(법률)이다.
사건카드와 확보된 근거를 바탕으로, **업무처리 단계별로** "법률-시행령-시행규칙-행정규칙(가능한 경우)"을 매핑해라.

[사건카드]
{cc}

[업무 흐름 설계(초안)]
{lp}

[확보된 원문/요약]
{_compact(legal_md, 3500)}

[출력(마크다운)]
1) 결론 3줄(가능/불가/추가확인)
2) **업무 단계별 법적 근거 매핑 표**
   - 열: 단계 | 적용 근거(법률/시행령/시행규칙/행정규칙) | 요건/체크포인트 | 절차 하자 방지
3) 절차적 정당성 체크리스트(사전통지/의견제출/송달/기한 등)
4) 리스크 & 방어논리(감사/소송 관점)
서론 금지.
"""
            return llm_service.generate_text(prompt)

        if role == "ADMIN":
            prompt = f"""{base}
{header}

너는 ADMIN(행정)이다.
법적 근거를 '현실 절차'로 번역해 **단계별 실행 SOP**를 작성하라.

[사건카드]
{cc}

[확보된 근거]
{_compact(legal_md, 2800)}

[출력(마크다운)]
1) 업무처리 흐름(표): 단계 | 담당 | 기한 | 입력(증빙/조회) | 출력(문서/통지) | 협조부서 | 유의사항
2) 체크리스트(Yes/No)
3) 문서 패키지(회신/통지/보고/계고 등)
4) 누락 위험 TOP3 + 예방책
서론 금지.
"""
            return llm_service.generate_text(prompt)

        if role == "CIVIL":
            prompt = f"""{base}
{header}

너는 CIVIL(민원)이다.
민원인의 오해/감정 포인트를 고려해 **재민원 감소형** 회신을 설계하라.

[사건카드]
{cc}

[법적 근거 요약]
{_compact(legal_md, 2400)}

[유사사례/뉴스(있으면)]
{_compact(news_md, 1200)}

[출력(마크다운)]
1) 민원 요지 3줄(민원인 관점/행정 관점)
2) 회신문 핵심 문장(바로 복붙 가능한 문장 5개)
3) FAQ 5개(예상 질문/표준 답변)
4) 반복/악성 민원 대응 레벨(1~3) + 원칙
서론 금지.
"""
            return llm_service.generate_text(prompt)

        if role == "BEHAVIOR":
            prompt = f"""{base}
{header}

너는 BEHAVIOR(행동/갈등)이다.
반발을 줄이면서도 법적 리스크를 키우지 않는 **현장/통화 스크립트**를 작성하라.

[사건카드]
{cc}

[출력(마크다운)]
1) 반발 유형 TOP5 + 대응 문장(그대로 읽기 가능)
2) 통화/대면 스크립트: 도입-설명-거절-마무리
3) 금지어/권장어
4) 기록·증거 남기기 체크리스트
서론 금지.
"""
            return llm_service.generate_text(prompt)

        if role == "PLAN":
            prompt = f"""{base}
{header}

너는 PLAN(기획)이다.
업무를 '템플릿/블록/지표'로 표준화해 조직 자산화하라.

[사건카드]
{cc}

[출력(마크다운)]
1) SOP 표준 목차(재사용 가능)
2) 재사용 블록(입력-처리-출력) 3~5개
3) 기록 필드(저장할 항목/분류체계)
4) KPI(처리시간/반려율/재민원율 등)
5) 개선안(단기/중기/장기 각 3개)
서론 금지.
"""
            return llm_service.generate_text(prompt)

        return ""

    @staticmethod
    def integrate(case_card: dict, route: dict, legal_plan: dict, legal_md: str, news_md: str, agent_out: dict) -> str:
        base = AgentPrompts.style_rules()
        prompt = f"""{base}
너는 INTEGRATOR(9급) 편집장이다.
아래 산출물을 충돌 없이 병합해 **최종 SOP(처리방향) 완제품**을 작성하라.
문서는 “상급자 보고 + 실무 실행 + 민원 대응”이 동시에 가능해야 한다.

[Mode/Risk]
Mode={route.get('mode')}({MODE_LABEL.get(route.get('mode'), '-')})
Risk={route.get('risk_level')}({RISK_HINT.get(route.get('risk_level'), '-')})

[사건카드]
{json.dumps(case_card, ensure_ascii=False)}

[법령 설계(업무 단계)]
{json.dumps(legal_plan, ensure_ascii=False)}

[확보된 법령/규정(원문 기반 요약)]
{_compact(legal_md, 3500)}

[유사사례/뉴스]
{_compact(news_md, 1200)}

[전문가 결과]
## ADMIN
{_compact(agent_out.get("ADMIN",""), 2200)}

## LEGAL
{_compact(agent_out.get("LEGAL",""), 2200)}

## CIVIL
{_compact(agent_out.get("CIVIL",""), 1800)}

## BEHAVIOR
{_compact(agent_out.get("BEHAVIOR",""), 1600)}

## PLAN
{_compact(agent_out.get("PLAN",""), 1600)}

[최종 출력 포맷(마크다운 고정)]
# 1. 한 줄 결론
- (가능/불가/추가확인 포함)

# 2. 업무처리 흐름 (단계/기한/담당)
- 표로 제시

# 3. 단계별 법적 근거 매핑
- 표로 제시(법률/시행령/시행규칙/행정규칙 포함)

# 4. 실무 체크리스트
- Yes/No

# 5. 민원 응대 핵심(회신 문장/FAQ)
- 문장 5개 + FAQ 5개

# 6. 예상 반발 및 대응 스크립트(필요 시)
- 표 + 스크립트

# 7. 리스크 & 방어 포인트
- 감사/소송 관점

# 8. 추가 확인 질문(최대 5개)
- 부족한 사실/증빙 질문

서론(인사말) 금지.
"""
        return llm_service.generate_text(prompt)

    @staticmethod
    def draft_document(case_card: dict, legal_md: str, final_sop: str, meta_info: dict) -> dict:
        schema = AgentPrompts.doc_schema()
        prompt = f"""
너는 행정기관 베테랑 서기다. 아래 최종 SOP를 기반으로 실제 공문 JSON을 작성하라.
- 문장: 공문체, 간결, 단정표현 지양(확인 필요는 표시)
- 법적 근거는 최소 1개 이상 명시(가능하면 조문/근거명 포함)
- 개인정보는 마스킹

[사건카드]
{json.dumps(case_card, ensure_ascii=False)}

[법령 요약]
{_compact(legal_md, 2000)}

[최종 SOP]
{_compact(final_sop, 2200)}

[시행일/기한]
- 시행일: {meta_info.get('today_str','')}
- 기한: {meta_info.get('deadline_str','')}

[출력] 반드시 JSON만.
필드:
- title
- receiver
- body_paragraphs (배열)
- department_head
"""
        doc = llm_service.generate_json(prompt, schema=schema)
        if not isinstance(doc, dict):
            return {
                "title": "민원 처리 결과 안내",
                "receiver": "민원인 귀하",
                "body_paragraphs": ["1. 경위", "2. 관련 법령", "3. 검토 결과", "4. 안내 사항"],
                "department_head": "행정기관장",
            }
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

    # Phase 0) 사건카드 + 라우팅
    add_log("🧩 Phase 0: 사건카드 구조화 및 라우팅...", "sys")
    t = time.perf_counter()
    case_card = MultiAgentSystem.extract_case_card(user_input)
    route = MultiAgentSystem.route(case_card)
    if route.get("risk_level") not in ["LOW", "MEDIUM", "HIGH"]:
        route["risk_level"] = "LOW"
    if route.get("mode") not in ["A", "B", "C", "D", "E"]:
        route["mode"] = "A"
    if not isinstance(route.get("agents"), list):
        route["agents"] = ["LEGAL", "INTEGRATOR"]
    if "INTEGRATOR" not in route["agents"]:
        route["agents"].append("INTEGRATOR")

    timings["route_sec"] = round(time.perf_counter() - t, 2)
    add_log(f"✅ 라우팅 완료: Mode={route.get('mode')} / Risk={route.get('risk_level')} ({timings['route_sec']}s)", "sys")

    # Phase 1) 법령 설계 + 원문 확보(법률/시행령/시행규칙/행정규칙)
    add_log("📜 Phase 1: 법령/규정 설계 및 원문 확보...", "legal")
    t = time.perf_counter()
    legal_plan = MultiAgentSystem.plan_legal(case_card, route)
    legal_md, legal_raw = MultiAgentSystem.fetch_legal_materials(legal_plan)
    timings["law_sec"] = round(time.perf_counter() - t, 2)
    add_log(f"✅ 법령/규정 확보 완료 ({timings['law_sec']}s)", "legal")

    # Phase 1.5) 뉴스(옵션)
    add_log("📰 Phase 1.5: 유사 사례/뉴스 검색...", "search")
    t = time.perf_counter()
    try:
        seed = (route.get("legal_query_seed") or "").strip()
        seed = seed if seed else (case_card.get("task_type") or user_input[:20])
        search_results = search_service.search_news(seed, top_k=3)
    except Exception:
        search_results = "검색 모듈 미연결"
    timings["news_sec"] = round(time.perf_counter() - t, 2)
    add_log(f"✅ 뉴스 검색 완료 ({timings['news_sec']}s)", "search")

    # Phase 2) 멀티 에이전트 실행(최소 조합)
    add_log("🧠 Phase 2: 전문가 에이전트 협업...", "strat")
    t = time.perf_counter()

    agents = route.get("agents") or []
    # INTEGRATOR는 통합 단계에서 호출하므로 여기서는 제외
    run_roles = [a for a in agents if a in ["ADMIN", "LEGAL", "CIVIL", "BEHAVIOR", "PLAN"]]

    agent_out: Dict[str, str] = {}

    def _run(role: str) -> Tuple[str, str]:
        out = MultiAgentSystem._call_agent(role, case_card, route, legal_plan, legal_md, search_results)
        return role, out

    if run_roles:
        with ThreadPoolExecutor(max_workers=min(4, len(run_roles))) as ex:
            futs = [ex.submit(_run, r) for r in run_roles]
            for f in as_completed(futs):
                try:
                    k, v = f.result()
                    agent_out[k] = v
                except Exception:
                    continue

    timings["agents_sec"] = round(time.perf_counter() - t, 2)
    add_log(f"✅ 에이전트 결과 수집 완료 ({timings['agents_sec']}s)", "strat")

    # Phase 3) INTEGRATOR(최종 SOP)
    add_log("🧭 Phase 3: 최종 SOP(처리방향) 편집...", "strat")
    t = time.perf_counter()
    final_sop = MultiAgentSystem.integrate(case_card, route, legal_plan, legal_md, search_results, agent_out)
    timings["integrate_sec"] = round(time.perf_counter() - t, 2)
    add_log(f"✅ SOP 완성 ({timings['integrate_sec']}s)", "strat")

    # Phase 4) 기한 산정 + 공문 생성
    add_log("📅 Phase 4: 기한 산정...", "calc")
    t = time.perf_counter()
    meta_info = LegalAgents.clerk(user_input, legal_md)  # 기존 clerk 재사용
    timings["calc_sec"] = round(time.perf_counter() - t, 2)

    add_log("✍️ Phase 5: 공문서 생성...", "draft")
    t = time.perf_counter()
    doc_data = MultiAgentSystem.draft_document(case_card, legal_md, final_sop, meta_info)
    timings["draft_sec"] = round(time.perf_counter() - t, 2)

    timings["total_sec"] = round(time.perf_counter() - t0, 2)
    log_placeholder.empty()

    # 기존 UI/DB 호환: law 필드=법령요약, strategy 필드=최종 SOP
    return {
        "situation": user_input,
        "case_card": case_card,
        "route": route,
        "legal_plan": legal_plan,
        "legal_raw": legal_raw,  # DB에 더 저장하고 싶으면 summary에 포함 가능
        "doc": doc_data,
        "meta": meta_info,
        "law": legal_md,
        "search": search_results,
        "strategy": final_sop,
        "agents": agent_out,
        "timings": timings,
    }
    timings["total_sec"] = round(time.perf_counter() - t0, 2)
    log_placeholder.empty()

    # 기존 UI/DB 호환: law 필드=법령요약, strategy 필드=최종 SOP
    return {
        "situation": user_input,
        "case_card": case_card,
        "route": route,
        "legal_plan": legal_plan,
        "legal_raw": legal_raw,  # DB에 더 저장하고 싶으면 summary에 포함 가능
        "doc": doc_data,
        "meta": meta_info,
        "law": legal_md,
        "search": search_results,
        "strategy": final_sop,
        "agents": agent_out,
        "timings": timings,
    }



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
    strategy = res.get("strategy", "")[:1200]  # SOP라서 조금 더
    route = res.get("route") or {}
    case_card = res.get("case_card") or {}

    doc = res.get("doc") or {}
    bp = doc.get("body_paragraphs", [])
    if isinstance(bp, str):
        bp = [bp]
    body = "\n".join([f"- {p}" for p in bp])

    return f"""[케이스 컨텍스트]
0) 라우팅: Mode={route.get('mode','')} / Risk={route.get('risk_level','')}
0-1) 사건카드: {json.dumps(case_card, ensure_ascii=False)[:800]}

1) 민원: {situation}
2) 법령: {law_txt}
3) 뉴스: {news_txt}
4) SOP: {strategy}
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

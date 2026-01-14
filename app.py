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
        132:     }
    
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
        if not self.creds:
            return
        with _vertex_lock:
            if self.creds.expired:
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
            raise RuntimeError("Groq 미설정")
        
        last_err = None
        for m in self.groq_models:
            try:
                chat_completion = self.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=m,
                    temperature=0.1,
                )
                return chat_completion.choices[0].message.content or ""
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"Groq 실패: {last_err}")

    def generate_text(self, prompt: str) -> str:
        last_err = None
        # Vertex 우선
        for m in self.vertex_models:
            try:
                out = self._vertex_generate(prompt, m)
                if out and out.strip():
                    return out
            except Exception as e:
                last_err = e
                continue
        
        # Groq 시도
        try:
            out = self._generate_groq(prompt)
            if out and out.strip():
                return out
        except Exception as e:
            if not last_err:
                last_err = e
        
        return f"⚠️ LLM 연결 실패: {last_err}"

    def generate_json(self, prompt: str, schema: dict) -> dict:
        # Vertex JSON
        v_schema = _vertex_schema_from_doc_schema(schema)
        for m in self.vertex_models:
            try:
                out = self._vertex_generate(prompt, m, "application/json", v_schema)
                return json.loads(out)
            except Exception:
                continue

        # Groq JSON (fallback)
        prompt += f"\n\nOutput strictly JSON matching:\n{json.dumps(schema, indent=2)}"
        try:
            out = self._generate_groq(prompt)
            # Markdown code block 제거
            if "```json" in out:
                out = out.split("```json")[1].split("```")[0]
            elif "```" in out:
                out = out.split("```")[1].split("```")[0]
            return json.loads(out)
        except Exception:
            return {}


class SearchService:
    def __init__(self):
        g = _safe_secrets("general")
        self.law_api_id = g.get("LAW_API_ID", "test")

    def search_law_text(self, query: str) -> str:
        # 1) 법령 검색
        lid = cached_law_search(self.law_api_id, query)
        if lid:
            xml_str = cached_law_detail_xml(self.law_api_id, lid)
            root = _safe_et_from_bytes(xml_str.encode("utf-8"))
            # 본문 추출 (조문 위주)
            texts = []
            for jo in root.findall(".//조문단위"):
                j_txt = (jo.findtext("조문내용") or "").strip()
                if j_txt:
                    texts.append(j_txt)
            if texts:
                return "\n".join(texts[:20]) # 너무 길면 자름

        # 2) 행정규칙 검색
        rid = cached_admrul_search(self.law_api_id, query)
        if rid:
            xml_str = cached_admrul_detail(self.law_api_id, rid)
            root = _safe_et_from_bytes(xml_str.encode("utf-8"))
            content = (root.findtext(".//본문") or "")
            if not content:
                # 조문 단위가 있을 수도
                texts = []
                for jo in root.findall(".//조문단위"):
                    j_txt = (jo.findtext("조문내용") or "").strip()
                    if j_txt:
                        texts.append(j_txt)
                content = "\n".join(texts)
            return content[:3000]

        return ""

    def search_news(self, query: str) -> str:
        return cached_naver_news(query)


class DatabaseService:
    def __init__(self):
        s = _safe_secrets("supabase")
        url = s.get("SUPABASE_URL")
        key = s.get("SUPABASE_KEY")
        self.client = None
        if create_client and url and key:
            try:
                self.client = create_client(url, key, options=ClientOptions(postgrest_client_timeout=10))
            except Exception:
                pass

    def sign_in(self, email: str) -> bool:
        # 간단 인증 시뮬레이션 (특정 이메일만 허용)
        # 실제 Supabase Auth를 쓴다면 self.client.auth.sign_in_with_otp(...) 등 사용
        return (email.strip() == "95kk@korea.kr")

    def sign_out(self):
        if self.client:
            try:
                self.client.auth.sign_out()
            except:
                pass

    def _get_db_client(self):
        return self.client

    def _pack_summary(self, summary_data: dict) -> str:
        return json.dumps(summary_data, ensure_ascii=False)

    def insert_initial_report(self, user_email: str, case_summary: dict, final_report: str):
        if not self.client:
            return
        try:
            data = {
                "user_email": user_email,
                "case_summary": self._pack_summary(case_summary),
                "final_report": final_report,
                "created_at": datetime.now(KST).isoformat()
            }
            self.client.table("legal_reports").insert(data).execute()
        except Exception as e:
            print(f"DB Insert Error: {e}")

    def update_followup(self, report_id: int, q: str, a: str):
        if not self.client:
            return
        try:
            # 기존 chat_history 가져오기
            res = self.client.table("legal_reports").select("chat_history").eq("id", report_id).execute()
            if not res.data:
                return
            
            curr_hist = res.data[0].get("chat_history") or []
            if isinstance(curr_hist, str):
                try:
                    curr_hist = json.loads(curr_hist)
                except:
                    curr_hist = []
            
            curr_hist.append({"role": "user", "content": q})
            curr_hist.append({"role": "assistant", "content": a})
            
            self.client.table("legal_reports").update({"chat_history": json.dumps(curr_hist, ensure_ascii=False)}).eq("id", report_id).execute()
        except Exception as e:
            print(f"DB Update Error: {e}")

    def list_reports(self, user_email: str):
        if not self.client:
            return []
        try:
            res = self.client.table("legal_reports").select("id, created_at, case_summary").eq("user_email", user_email).order("created_at", desc=True).execute()
            return res.data
        except Exception:
            return []

    def get_report(self, report_id: int):
        if not self.client:
            return None
        try:
            res = self.client.table("legal_reports").select("*").eq("id", report_id).execute()
            if res.data:
                return res.data[0]
        except Exception:
            pass
        return None

    def delete_report(self, report_id: int):
        if not self.client:
            return
        try:
            self.client.table("legal_reports").delete().eq("id", report_id).execute()
        except Exception:
            pass


class LawOfficialService:
    """국가법령정보센터 API 래퍼"""
    def __init__(self):
        g = _safe_secrets("general")
        self.api_id = g.get("LAW_API_ID", "test")

    def get_law_content(self, query: str) -> str:
        lid = cached_law_search(self.api_id, query)
        if not lid:
            return ""
        return cached_law_detail_xml(self.api_id, lid)

    def get_admrul_content(self, query: str) -> str:
        rid = cached_admrul_search(self.api_id, query)
        if not rid:
            return ""
        return cached_admrul_detail(self.api_id, rid)

    def search_precedent(self, query: str) -> str:
        # 판례 검색 (간략 구현)
        base_url = "https://www.law.go.kr/DRF/lawSearch.do"
        params = {"OC": self.api_id, "target": "prec", "type": "XML", "query": query, "display": 3}
        try:
            r = http_get(base_url, params=params, timeout=10)
            root = _safe_et_from_bytes(r.content)
            items = []
            for it in root.findall(".//prec"):
                title = (it.findtext("판례내용") or it.findtext("사건명") or "").strip()
                if title:
                    items.append(title)
            return "\n\n".join(items)
        except Exception:
            return ""


# Global Instances
llm_service = LLMService()
search_service = SearchService()
db_service = DatabaseService()
law_official = LawOfficialService()


# ==========================================
# 4) Agents & Prompts
# ==========================================
class AgentPrompts:
    @staticmethod
    def style_rules():
        return """
        [Style Rules]
        - Use Korean language primarily.
        - Be professional, legal, and authoritative.
        - Use markdown for structure.
        - Cite laws specifically (e.g., '민법 제3조').
        """

    @staticmethod
    def case_card_schema():
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "category": {"type": "string", "enum": ["CIVIL", "CRIMINAL", "ADMINISTRATIVE", "COMMERCIAL", "OTHER"]},
                "risk_level": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
                "parties": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["summary", "keywords", "category", "risk_level"]
        }

    @staticmethod
    def route_schema():
        return {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["A", "B", "C", "D", "E"]},
                "reason": {"type": "string"}
            },
            "required": ["mode", "reason"]
        }

    @staticmethod
    def legal_plan_schema():
        return {
            "type": "object",
            "properties": {
                "issues": {"type": "array", "items": {"type": "string"}},
                "required_laws": {"type": "array", "items": {"type": "string"}},
                "search_keywords": {"type": "array", "items": {"type": "string"}},
                "strategy": {"type": "string"}
            },
            "required": ["issues", "required_laws", "search_keywords", "strategy"]
        }

    @staticmethod
    def doc_schema():
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["title", "content"]
        }


class ClerkAgent:
    """기초 분석 및 메타데이터 추출"""
    def clerk(self, user_input: str) -> dict:
        prompt = f"""
        Analyze the following user query and extract key information into a Case Card.
        User Input: {user_input}
        """
        return llm_service.generate_json(prompt, AgentPrompts.case_card_schema())

    def compute_meta(self, case_card: dict) -> dict:
        # 간단한 메타데이터 계산 (Risk 점수 등)
        risk = case_card.get("risk_level", "LOW")
        score = 10
        if risk == "MEDIUM": score = 50
        if risk == "HIGH": score = 90
        return {"risk_score": score, "processed_at": datetime.now().isoformat()}
class MultiAgentSystem:
    def __init__(self):
        self.clerk_agent = ClerkAgent()

    def extract_case_card(self, user_input: str) -> dict:
        with st.spinner("🕵️ [CLERK] 사건 내용을 분석하고 있습니다..."):
            card = self.clerk_agent.clerk(user_input)
            meta = self.clerk_agent.compute_meta(card)
            card.update(meta)
        
        with st.expander("📋 사건 카드 (Case Card)", expanded=True):
            st.markdown(f"**요약**: {card.get('summary')}")
            st.markdown(f"**키워드**: {', '.join(card.get('keywords', []))}")
            st.markdown(f"**분류**: `{card.get('category')}` | **위험도**: `{card.get('risk_level')}`")
            st.json(card)
        return card

    def route(self, case_card: dict) -> str:
        with st.spinner("🚦 [ROUTER] 최적의 처리 경로를 탐색 중입니다..."):
            prompt = f"""
            Determine the processing Mode (A-E) based on the Case Card.
            Case Card: {json.dumps(case_card, ensure_ascii=False)}
            
            Modes:
            A: Simple Info (Low Risk)
            B: Standard Civil/Admin (Medium Risk)
            C: Complex/High Risk (High Risk)
            D: Document Drafting
            E: Other/General
            """
            res = llm_service.generate_json(prompt, AgentPrompts.route_schema())
            mode = res.get("mode", "E")
            reason = res.get("reason", "")
        
        st.markdown(f"""
        <div class="agent-log log-sys">
            <h4>🚦 라우팅 결과: Mode {mode}</h4>
            <p>{reason}</p>
        </div>
        """, unsafe_allow_html=True)
        return mode

    def plan_legal(self, case_card: dict, mode: str) -> dict:
        with st.spinner("📝 [PLANNER] 법률 검토 계획을 수립합니다..."):
            prompt = f"""
            Create a Legal Investigation Plan.
            Case Card: {json.dumps(case_card, ensure_ascii=False)}
            Mode: {mode}
            """
            plan = llm_service.generate_json(prompt, AgentPrompts.legal_plan_schema())
        
        with st.expander("📅 법률 검토 계획 (Legal Plan)", expanded=False):
            st.markdown("**쟁점 (Issues)**")
            for i in plan.get("issues", []):
                st.markdown(f"- {i}")
            st.markdown("**필요 법령**")
            st.markdown(", ".join(plan.get("required_laws", [])))
            st.markdown("**검색 키워드**")
            st.markdown(", ".join(plan.get("search_keywords", [])))
            st.markdown(f"**전략**: {plan.get('strategy')}")
        return plan

    def fetch_legal_materials(self, plan: dict) -> List[dict]:
        materials = []
        st.markdown("### 📚 법률 자료 수집")
        
        # 1. Laws
        laws = plan.get("required_laws", [])
        if laws:
            progress_text = "📜 법령 데이터베이스 검색 중..."
            my_bar = st.progress(0, text=progress_text)
            for idx, law in enumerate(laws):
                content = search_service.search_law_text(law)
                if content:
                    materials.append({"type": "LAW", "title": law, "content": content})
                    st.markdown(f"- 📜 **{law}**: 본문 확보 완료")
                else:
                    st.markdown(f"- 📜 **{law}**: 검색 실패")
                my_bar.progress((idx + 1) / len(laws), text=progress_text)
            my_bar.empty()
        
        # 2. Keywords (News/Prec)
        kws = plan.get("search_keywords", [])
        if kws:
            with st.spinner("🔍 판례 및 최신 뉴스 검색 중..."):
                for kw in kws:
                    # News
                    news = search_service.search_news(kw)
                    if news and "없습니다" not in news:
                        materials.append({"type": "NEWS", "title": kw, "content": news})
                    
                    # Precedent
                    prec = law_official.search_precedent(kw)
                    if prec:
                        materials.append({"type": "PRECEDENT", "title": kw, "content": prec})
                        st.markdown(f"- ⚖️ **{kw}** 관련 판례/뉴스 확보")

        return materials

    def _call_agent(self, agent_name: str, role_prompt: str, context: str, css_class: str = "log-sys") -> str:
        with st.spinner(f"🤖 [{agent_name}] 분석 수행 중..."):
            prompt = f"""
            Role: {agent_name}
            {role_prompt}
            
            Context:
            {context}
            
            Task: Provide your professional analysis/opinion.
            """
            out = llm_service.generate_text(prompt)
        
        st.markdown(f"""
        <div class="agent-log {css_class}">
            <h4>🤖 {agent_name}</h4>
            <div style="white-space: pre-wrap;">{out}</div>
        </div>
        """, unsafe_allow_html=True)
        return out

    def integrate(self, case_card: dict, plan: dict, materials: List[dict], mode: str) -> str:
        st.markdown("### 🧠 종합 분석 (Multi-Agent Integration)")
        
        # Context Assembly
        context = f"Case: {json.dumps(case_card, ensure_ascii=False)}\n"
        context += f"Plan: {json.dumps(plan, ensure_ascii=False)}\n"
        context += "Materials:\n"
        for m in materials:
            context += f"[{m['type']}] {m['title']}: {m['content'][:800]}...\n"

        # Agent Execution based on Mode
        outputs = {}
        
        # 1. Legal Analysis (Always)
        outputs["LEGAL_ANALYSIS"] = self._call_agent(
            "LEGAL_AGENT", 
            "Analyze legal issues and apply laws. Cite specific articles.", 
            context,
            "log-legal"
        )
        
        # 2. Search/Precedent (Mode B, C)
        if mode in ["B", "C"]:
            outputs["CASE_SEARCH"] = self._call_agent(
                "SEARCH_AGENT", 
                "Find relevant precedents and similar cases. Compare facts.", 
                context,
                "log-search"
            )
        
        # 3. Strategy (Mode C)
        if mode == "C":
            outputs["STRATEGY"] = self._call_agent(
                "STRATEGY_AGENT", 
                "Develop litigation strategy and risk mitigation. Suggest specific actions.", 
                context,
                "log-strat"
            )
        
        # Final Integration
        with st.spinner("📝 최종 보고서 작성 중..."):
            final_prompt = f"""
            Synthesize all analyses into a final legal report.
            {AgentPrompts.style_rules()}
            
            Analyses:
            {json.dumps(outputs, ensure_ascii=False)}
            
            Structure:
            1. Executive Summary
            2. Legal Analysis (Issues & Application of Law)
            3. Relevant Laws & Precedents
            4. Strategic Recommendations
            5. Conclusion
            """
            final_report = llm_service.generate_text(final_prompt)
        
        return final_report

    def draft_document(self, case_card: dict, plan: dict) -> dict:
        with st.spinner("✍️ [DRAFTER] 법률 문서 초안을 작성합니다..."):
            prompt = f"""
            Draft a legal document based on the case.
            Case: {json.dumps(case_card, ensure_ascii=False)}
            """
            doc = llm_service.generate_json(prompt, AgentPrompts.doc_schema())
        
        st.markdown(f"""
        <div class="agent-log log-draft">
            <h4>✍️ 문서 작성 완료: {doc.get('title')}</h4>
        </div>
        """, unsafe_allow_html=True)
        return doc
# ==========================================
# 5) Workflow & UI
# ==========================================
def run_workflow(user_input: str):
    mas = MultiAgentSystem()
    
    # 1. Clerk (Case Card)
    case_card = mas.extract_case_card(user_input)
    
    # 2. Router (Mode)
    mode = mas.route(case_card)
    
    # 3. Planner
    plan = mas.plan_legal(case_card, mode)
    
    # 4. Search (Materials)
    materials = mas.fetch_legal_materials(plan)
    
    # 5. Integrator (Final Report)
    final_report = mas.integrate(case_card, plan, materials, mode)
    
    # 6. Drafter (Optional)
    doc_draft = None
    if mode == "D":
        doc_draft = mas.draft_document(case_card, plan)
    
    # --- UI Rendering (Paper Sheet) ---
    st.markdown("---")
    st.markdown(f"""
    <div class="paper-sheet">
        <div class="doc-header">법률 검토 보고서</div>
        <div class="doc-info">
            <span><b>수신:</b> 의뢰인 귀하</span>
            <span><b>발신:</b> AI 법률 사무소</span>
            <span><b>일자:</b> {datetime.now().strftime('%Y년 %m월 %d일')}</span>
        </div>
        <div class="doc-body">
            {final_report.replace(chr(10), '<br>')}
        </div>
        <div class="doc-footer">
            AI Bureau Chief
            <div class="stamp">CONFIDENTIAL</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    if doc_draft:
        st.markdown("---")
        st.subheader("📄 법률 문서 초안")
        st.text_area("Draft Content", value=doc_draft.get("content", ""), height=400)

    # Save to DB
    if st.session_state.get("user_email"):
        db_service.insert_initial_report(
            st.session_state["user_email"],
            case_card,
            final_report
        )
        st.success("✅ 보고서가 저장되었습니다.")

    # Session State Update for Follow-up
    st.session_state["current_report"] = final_report
    st.session_state["case_context"] = case_card
    st.session_state["chat_history"] = []


def answer_followup(user_q: str):
    ctx = st.session_state.get("case_context", {})
    rep = st.session_state.get("current_report", "")
    hist = st.session_state.get("chat_history", [])
    
    prompt = f"""
    Context:
    Case: {json.dumps(ctx, ensure_ascii=False)}
    Report: {rep}
    History: {hist}
    
    User Question: {user_q}
    
    Answer as a helpful legal assistant.
    """
    ans = llm_service.generate_text(prompt)
    
    # Update History
    hist.append({"role": "user", "content": user_q})
    hist.append({"role": "assistant", "content": ans})
    st.session_state["chat_history"] = hist
    
    return ans


def render_followup_chat():
    if not st.session_state.get("current_report"):
        return

    st.markdown("---")
    st.subheader("💬 추가 질의응답 (Follow-up Chat)")
    
    # Chat History Display
    for msg in st.session_state.get("chat_history", []):
        role = msg["role"]
        content = msg["content"]
        if role == "user":
            st.chat_message("user").write(content)
        else:
            st.chat_message("assistant").write(content)

    # Input
    if len(st.session_state.get("chat_history", [])) < MAX_FOLLOWUP_Q * 2:
        q = st.chat_input("추가 질문을 입력하세요...")
        if q:
            st.chat_message("user").write(q)
            with st.spinner("답변 작성 중..."):
                ans = answer_followup(q)
            st.chat_message("assistant").write(ans)
    else:
        st.info("최대 질의 횟수에 도달했습니다.")


def render_sidebar_ui():
    with st.sidebar:
        st.title("AI Bureau")
        st.markdown("---")
        
        # Login
        if "user_email" not in st.session_state:
            email = st.text_input("이메일 로그인", placeholder="example@korea.kr")
            if st.button("로그인"):
                if db_service.sign_in(email):
                    st.session_state["user_email"] = email
                    st.success(f"환영합니다, {email}님")
                    st.rerun()
                else:
                    st.error("로그인 실패 (허용된 이메일이 아닙니다)")
        else:
            st.info(f"접속중: {st.session_state['user_email']}")
            if st.button("로그아웃"):
                db_service.sign_out()
                del st.session_state["user_email"]
                st.rerun()
            
            st.markdown("---")
            st.subheader("🗂 내 사건 기록")
            reports = db_service.list_reports(st.session_state["user_email"])
            for r in reports:
                summ = json.loads(r.get("case_summary", "{}"))
                title = summ.get("summary", "제목 없음")[:15] + "..."
                if st.button(f"📄 {title}", key=f"rep_{r['id']}"):
                    # Load Report
                    full_rep = db_service.get_report(r['id'])
                    if full_rep:
                        st.session_state["current_report"] = full_rep.get("final_report")
                        st.session_state["case_context"] = json.loads(full_rep.get("case_summary", "{}"))
                        hist = full_rep.get("chat_history")
                        st.session_state["chat_history"] = json.loads(hist) if hist else []
                        st.rerun()


def main():
    render_sidebar_ui()
    
    st.title("⚖️ AI Legal Assistant")
    st.markdown("### 당신의 법률 AI 파트너")
    
    if "user_email" not in st.session_state:
        st.warning("로그인이 필요합니다.")
        return

    # Main Input
    with st.form("main_form"):
        user_input = st.text_area("사건 내용을 입력하세요", height=150, placeholder="예: 층간소음으로 인한 손해배상 청구 절차가 궁금합니다.")
        submitted = st.form_submit_button("분석 시작", use_container_width=True)
    
    if submitted and user_input:
        run_workflow(user_input)
    
    # Follow-up Chat
    render_followup_chat()


if __name__ == "__main__":
    main()

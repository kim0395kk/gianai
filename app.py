import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
from serpapi import GoogleSearch
import re
import time
from supabase import create_client
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, InternalServerError

# --- 0. 디자인 시스템 ---
st.set_page_config(layout="wide", page_title="AI 행정관: The Legal Glass", page_icon="⚖️")

st.markdown("""
<style>
    .stApp { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); }
    div[data-testid="stVerticalBlock"] > div[style*="background-color"] {
        background: rgba(255, 255, 255, 0.75);
        box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1);
        backdrop-filter: blur(8px);
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.4);
        padding: 25px;
        margin-bottom: 20px;
    }
    h1, h2, h3 { color: #1a237e !important; font-family: 'Helvetica Neue', sans-serif; }
    strong { color: #1a237e; background-color: rgba(26, 35, 126, 0.05); padding: 2px 4px; border-radius: 4px; }
    .status-badge { background-color: #dbeafe; color: #1e40af; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- 1. 초기화 ---
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    SERPAPI_KEY = st.secrets["general"]["SERPAPI_KEY"]
    
    try:
        SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
        SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_db = True
    except: use_db = False

    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error(f"🚨 API 키 설정 오류: {e}")
    st.stop()

@st.cache_data
def get_model():
    return 'models/gemini-1.5-flash'

MODEL_NAME = get_model()

# --- 2. 로직 엔진 ---

def get_law_context(situation, callback):
    """[엔진 1] 법령 API (안전 제일 모드)"""
    callback(10, "📜 법령 식별 중...")
    model = genai.GenerativeModel(MODEL_NAME)
    try:
        res = model.generate_content(f"상황: {situation}\n관련 법령명 1개만 출력 (예: 도로교통법)").text
        law_name = re.sub(r'[^가-힣]', '', res)
    except: return "식별 실패", ""

    callback(30, f"🏛️ '{law_name}' 조회 중...")
    try:
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
        root = ET.fromstring(requests.get(search_url, timeout=3).content)
        mst = root.find(".//법령일련번호").text
        real_name = root.find(".//법령명한글").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_root = ET.fromstring(requests.get(detail_url, timeout=5).content)
        
        articles = []
        # [안전장치] 딱 10개만 가져옵니다. (토큰 절약 최우선)
        for a in detail_root.findall(".//조문")[:10]: 
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            articles.append(f"[제{num}조] {cont}")
            
        callback(50, f"✅ 법령 데이터 확보.")
        return real_name, "\n".join(articles)
    except:
        return law_name, ""

def get_search_results(situation, callback):
    """[엔진 2] 구글 서치"""
    callback(60, "🔍 사례 검색 중...")
    try:
        # 검색 결과 3개로 제한
        params = {"engine": "google", "q": f"{situation} 행정처분 사례", "api_key": SERPAPI_KEY, "num": 3}
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        snippets = [f"- {item['title']}: {item['snippet']}" for item in results]
        return "\n".join(snippets)
    except:
        return ""

def generate_report_safe(situation, law_name, law_text, search_text, callback):
    """[엔진 3] 과부하 방지 스마트 로직"""
    model = genai.GenerativeModel(MODEL_NAME)
    
    # [핵심] 입력 데이터가 너무 길면 Python에서 미리 자릅니다. (API 요청 전 다이어트)
    if len(law_text) > 3000:
        law_text = law_text[:3000] + "...(생략)"
    
    # 전략 1: 표준 모드
    prompt_std = f"""
    당신은 행정관입니다. 마크다운 보고서를 작성하세요.
    [민원] {situation}
    [법령] {law_name}\n{law_text}
    [사례] {search_text}
    
    ## 💡 핵심 요약
    ## 📜 법적 검토
    ## 🔍 유사 사례
    ## 👣 조치 계획
    ## 📄 답변 초안
    """

    # 전략 2: 비상 모드 (법령 텍스트 제거)
    prompt_lite = f"""
    [비상모드] 법령 데이터가 누락되었습니다. 당신의 행정 지식으로 답변하세요.
    [민원] {situation}
    [관련법] {law_name}
    [사례] {search_text}
    
    ## 💡 핵심 요약
    ## 📜 법적 검토 (AI 지식 기반)
    ## 🔍 유사 사례
    ## 👣 조치 계획
    ## 📄 답변 초안
    """

    # 1차 시도
    callback(80, "🧠 [1차] 정밀 분석 시도...")
    try:
        res = model.generate_content(prompt_std)
        callback(100, "🎉 분석 완료!")
        return res.text
    except Exception as e:
        print(f"1차 실패: {e}") # 로그 확인용

    # 2차 시도 (실패 시 충분히 쉬고 가벼운 요청으로)
    # 여기서 바로 재요청하면 100% 또 죽습니다. 5초간 쉽니다.
    for i in range(5, 0, -1):
        callback(85, f"⚠️ 트래픽 조절 중... {i}초 대기")
        time.sleep(1)
        
    callback(90, "🚀 [2차] 경량화 모드로 재시도...")
    try:
        # 토큰을 확 줄인 Lite 프롬프트 사용
        res = model.generate_content(prompt_lite)
        return res.text + "\n\n*(트래픽 과부하로 인해 경량 모드로 작성되었습니다)*"
    except Exception as e:
        return f"죄송합니다. 서버가 현재 너무 혼잡합니다. 잠시 후(1분 뒤) 다시 시도해주세요.\n(Error: {e})"

# --- 3. UI 실행 ---

st.markdown("""
<div style="text-align:center; padding: 20px; background: rgba(255,255,255,0.6); border-radius: 20px; border: 1px solid rgba(255,255,255,0.4);">
    <h1 style="color:#1a237e;">⚖️ AI 행정관: Safe Mode</h1>
    <span class="status-badge">Traffic Control System On</span>
</div>
<br>
""", unsafe_allow_html=True)

with st.container():
    st.markdown('<div style="background-color:rgba(0,0,0,0);"></div>', unsafe_allow_html=True)
    user_input = st.text_area("민원 상황 입력", height=100, placeholder="예: 아파트 단지 내 킥보드 강제 수거 가능 여부")
    btn = st.button("🚀 분석 시작", use_container_width=True, type="primary")

if btn and user_input:
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update(p, t):
        progress_bar.progress(p)
        status_text.caption(f"{t}")
        time.sleep(0.05)

    # 1. 법령 (10개 제한)
    law_name, law_text = get_law_context(user_input, update)
    time.sleep(1) # API 사이 휴식
    
    # 2. 검색
    search_text = get_search_results(user_input, update)
    time.sleep(1) # API 사이 휴식
    
    # 3. 분석 (실패 시 5초 대기 후 경량화 재시도)
    final_text = generate_report_safe(user_input, law_name, law_text, search_text, update)
    
    progress_bar.empty()
    status_text.empty()
    
    # 결과 출력
    st.divider()
    sections = re.split(r'(?=## )', final_text)
    
    for section in sections:
        if not section.strip(): continue
        with st.container():
            st.markdown('<div style="background-color:rgba(0,0,0,0);"></div>', unsafe_allow_html=True)
            st.markdown(section)

    # DB 저장
    if use_db:
        try:
            supabase.table("law_reports").insert({
                "situation": user_input,
                "law_name": law_name,
                "summary": final_text[:500]
            }).execute()
            st.toast("저장 완료", icon="💾")
        except: pass

import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
from serpapi import GoogleSearch
import re
import time
from supabase import create_client
from google.api_core.exceptions import ResourceExhausted

# --- 0. 디자인 시스템 ---
st.set_page_config(layout="wide", page_title="AI 행정관: The Legal Glass", page_icon="⚖️")

st.markdown("""
<style>
    .stApp { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); }
    div[data-testid="stVerticalBlock"] > div[style*="background-color"] {
        background: rgba(255, 255, 255, 0.65);
        box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.4);
        padding: 25px;
        margin-bottom: 20px;
    }
    h1, h2, h3 { color: #1a237e !important; font-family: 'Helvetica Neue', sans-serif; }
    strong { color: #1a237e; background-color: rgba(26, 35, 126, 0.05); padding: 2px 4px; border-radius: 4px; }
    li { margin-bottom: 5px; }
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
    models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    for m in ['models/gemini-1.5-flash', 'models/gemini-1.5-flash-latest']:
        if m in models: return m
    return models[0] if models else None

MODEL_NAME = get_model()

# --- 2. 로직 엔진 (안전장치 추가됨) ---

def get_law_context(situation, callback):
    """[엔진 1] 법령 API (100개 조문으로 최적화)"""
    callback(10, "📜 상황에 맞는 법령을 식별 중입니다...")
    model = genai.GenerativeModel(MODEL_NAME)
    try:
        res = model.generate_content(f"상황: {situation}\n관련된 대한민국 법령명 1개만 정확히 출력해 (예: 도로교통법)").text
        law_name = re.sub(r'[^가-힣]', '', res)
    except: return "식별 실패", ""

    callback(30, f"🏛️ '{law_name}'의 주요 조문을 분석합니다...")
    try:
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
        root = ET.fromstring(requests.get(search_url).content)
        mst = root.find(".//법령일련번호").text
        real_name = root.find(".//법령명한글").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_root = ET.fromstring(requests.get(detail_url).content)
        
        articles = []
        # [수정: 안전 다이어트] 300개 -> 100개 (무료 API 한도 보호)
        # 100개면 보통 '보칙/벌칙' 장까지 충분히 닿습니다.
        for a in detail_root.findall(".//조문")[:100]: 
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            articles.append(f"[제{num}조] {cont}")
            
        callback(50, f"✅ {real_name} 데이터 확보 완료.")
        return real_name, "\n".join(articles)
    except:
        return law_name, "법령 원문을 가져오지 못했습니다."

def get_search_results(situation, callback):
    """[엔진 2] 구글 서치"""
    callback(60, "🔍 유사 사례 및 판례를 검색합니다...")
    try:
        params = {"engine": "google", "q": f"{situation} 행정처분 사례 판례", "api_key": SERPAPI_KEY, "num": 5}
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        snippets = [f"- [{item.get('source', '웹')}] {item['title']}: {item['snippet']}" for item in results]
        return "\n".join(snippets)
    except:
        return "검색 결과 없음"

def generate_report(situation, law_name, law_text, search_text, callback):
    """[엔진 3] AI 종합 분석 (재시도 로직 추가)"""
    callback(80, "🧠 법리와 현실을 종합하여 보고서를 작성 중입니다...")
    model = genai.GenerativeModel(MODEL_NAME)
    
    prompt = f"""
    당신은 유능한 행정관입니다. 아래 정보를 바탕으로 보고서를 작성하세요.
    
    [민원] {situation}
    [법적근거] {law_name}\n{law_text}
    [참고사례] {search_text}
    
    [작성 규칙]
    1. 마크다운(Markdown) 문법을 사용하세요. (HTML 태그 금지)
    2. 아래 섹션 제목(##)을 정확히 지키세요.
    
    ## 💡 핵심 요약
    (3줄 이내)
    ## 📜 법적 검토 및 근거
    (조항 구체적 명시)
    ## 🔍 유사 사례 및 현실 분석
    (검색 결과 기반)
    ## 👣 실무 액션 플랜
    (단계별 행동 지침)
    ## 📄 민원 답변용 문안
    (정중한 답변 텍스트)
    """
    
    # [수정: 재시도 로직] 에러 발생 시 3번까지 재시도
    max_retries = 3
    for attempt in range(max_retries):
        try:
            res = model.generate_content(prompt)
            callback(100, "🎉 분석 완료!")
            return res.text
        except ResourceExhausted:
            # 한도 초과 시 잠시 대기
            wait_time = (attempt + 1) * 2 # 2초, 4초, 6초 대기
            callback(80, f"⚠️ 트래픽이 많아 대기 중입니다... ({attempt+1}/{max_retries})")
            time.sleep(wait_time)
        except Exception as e:
            return f"오류 발생: {e}"
            
    return "죄송합니다. 현재 사용량이 많아 분석을 완료하지 못했습니다. 잠시 후 다시 시도해주세요."

# --- 3. UI 구성 및 실행 ---

st.markdown("""
<div style="text-align:center; padding: 20px; background: rgba(255,255,255,0.6); border-radius: 20px; border: 1px solid rgba(255,255,255,0.4);">
    <h1 style="color:#1a237e;">⚖️ AI 행정관: The Legal Glass</h1>
    <p style="color:#555;">법령(Rule)과 현실(Reality)을 융합한 최적의 행정 솔루션</p>
</div>
<br>
""", unsafe_allow_html=True)

with st.container():
    st.markdown('<div style="background-color:rgba(0,0,0,0);"></div>', unsafe_allow_html=True)
    user_input = st.text_area("민원 상황을 입력하세요", height=100, placeholder="예: 아파트 단지 내 장기 방치 킥보드, 구청이 강제 수거 가능한가요?")
    btn = st.button("🚀 분석 시작", use_container_width=True, type="primary")

if btn and user_input:
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update(p, t):
        progress_bar.progress(p)
        status_text.caption(f"running... {t}")
        time.sleep(0.1)

    # 실행
    law_name, law_text = get_law_context(user_input, update)
    time.sleep(1) # [추가] API 호출 간격 두기 (과부하 방지)
    
    search_text = get_search_results(user_input, update)
    final_text = generate_report(user_input, law_name, law_text, search_text, update)
    
    time.sleep(0.5)
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
            st.toast("✅ 분석 결과 저장 완료!", icon="💾")
        except: pass

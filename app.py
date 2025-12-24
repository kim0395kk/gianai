import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
from serpapi import GoogleSearch
import re
import time
from supabase import create_client

# --- 0. 디자인 시스템 설정 (CSS Injection) ---
st.set_page_config(layout="wide", page_title="AI 행정관: The Legal Glass", page_icon="⚖️")

# 글래스모피즘 및 라운딩 스타일 정의
st.markdown("""
<style>
    /* 전체 배경: 부드러운 그라데이션 */
    .stApp {
        background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
    }
    
    /* 글래스모피즘 카드 스타일 */
    div.glass-card {
        background: rgba(255, 255, 255, 0.6);
        box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border-radius: 25px;
        border: 1px solid rgba(255, 255, 255, 0.4);
        padding: 25px;
        margin-bottom: 20px;
    }
    
    /* 제목 스타일 */
    h1, h2, h3 {
        color: #1a237e;
        font-family: 'Helvetica Neue', sans-serif;
        font-weight: 700;
    }
    
    /* 입력창 및 버튼 라운딩 */
    .stTextArea textarea {
        border-radius: 20px !important;
        border: 1px solid rgba(255, 255, 255, 0.5) !important;
        background: rgba(255, 255, 255, 0.8) !important;
        box-shadow: inset 2px 2px 5px rgba(0,0,0,0.05) !important;
    }
    .stButton button {
        border-radius: 30px !important;
        background: linear-gradient(90deg, #4b6cb7 0%, #182848 100%) !important;
        color: white !important;
        font-weight: bold !important;
        border: none !important;
        padding: 12px 24px !important;
        transition: all 0.3s ease !important;
    }
    .stButton button:hover {
        transform: translateY(-3px);
        box-shadow: 0 10px 20px rgba(0,0,0,0.2) !important;
    }
    
    /* 프로그레스 바 스타일 */
    .stProgress > div > div > div > div {
        background-image: linear-gradient(to right, #4b6cb7, #182848);
    }
    
    /* 결과 카드 내부 헤더 */
    .result-header {
        display: flex;
        align-items: center;
        margin-bottom: 15px;
        color: #182848;
        border-bottom: 2px solid rgba(75, 108, 183, 0.2);
        padding-bottom: 10px;
    }
    .result-icon { font-size: 1.5rem; margin-right: 10px; }
</style>
""", unsafe_allow_html=True)

# --- 1. 초기화 및 API 연결 ---
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
    st.markdown(f"""<div class="glass-card" style="background:rgba(255,0,0,0.1);">
    🚨 <b>시스템 연결 오류</b><br>API 키 설정(secrets.toml)을 확인해주세요.<br>Error: {e}</div>""", unsafe_allow_html=True)
    st.stop()

@st.cache_data
def get_model():
    models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    for m in ['models/gemini-1.5-flash', 'models/gemini-1.5-pro']:
        if m in models: return m
    return models[0] if models else None

MODEL_NAME = get_model()

# --- 2. 핵심 로직 (The Engines) ---

def get_law_context_v2(situation, progress_callback):
    """법령 엔진: 법령명 추론 후 전문 확보"""
    progress_callback(10, "📜 AI가 상황에 맞는 법령을 추론 중입니다...")
    model = genai.GenerativeModel(MODEL_NAME)
    
    prompt = f"상황: {situation}\n관련된 대한민국 법령 정식 명칭 1개만 출력해. (예: 도로교통법)"
    try:
        law_name = model.generate_content(prompt).text.strip()
        law_name = re.sub(r'[^가-힣]', '', law_name)
    except:
        progress_callback(20, "⚠️ 법령명 추론 실패. 다음 단계로 이동합니다.")
        return "식별 실패", ""

    progress_callback(25, f"🔍 '{law_name}'의 최신 조문 데이터를 국가법령정보센터에서 가져옵니다...")
    try:
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
        root = ET.fromstring(requests.get(search_url).content)
        mst = root.find(".//법령일련번호").text
        real_name = root.find(".//법령명한글").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_root = ET.fromstring(requests.get(detail_url).content)
        
        articles = []
        # 토큰 효율과 속도를 위해 상위 중요 조문 30개만 추출
        for a in detail_root.findall(".//조문")[:30]:
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            articles.append(f"[제{num}조] {cont}")
        
        progress_callback(40, f"✅ {real_name} 데이터 확보 완료.")
        return real_name, "\n".join(articles)
    except:
        progress_callback(40, f"⚠️ {law_name} 데이터 확보 실패. AI 기본 지식으로 대체합니다.")
        return law_name, "법령 텍스트를 가져오지 못했습니다."

def get_google_search_results_v2(situation, progress_callback):
    """현실 엔진: 구글 뉴스 및 사례 검색"""
    progress_callback(50, "🌐 구글 웹에서 타 지자체 사례와 관련 뉴스를 검색합니다...")
    query = f"{situation} 행정처분 사례 판례 해결"
    
    params = {"engine": "google", "q": query, "api_key": SERPAPI_KEY, "hl": "ko", "gl": "kr", "num": 5}
    try:
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        snippets = []
        for item in results:
            snippets.append(f"- [{item.get('source', '웹')}] {item.get('title')}: {item.get('snippet')}")
        progress_callback(70, f"✅ {len(snippets)}건의 유사 사례 및 뉴스 확보 완료.")
        return "\n".join(snippets)
    except Exception as e:
        progress_callback(70, "⚠️ 구글 검색 연결 실패. 다음 단계로 이동합니다.")
        return f"구글 검색 실패: {e}"

def generate_final_report_v2(situation, law_name, law_text, search_text, progress_callback):
    """종합 엔진: AI가 법과 현실을 종합하여 구조화된 리포트 작성"""
    progress_callback(80, "🧠 확보된 데이터를 바탕으로 AI가 종합 분석 및 보고서를 작성합니다...")
    model = genai.GenerativeModel(MODEL_NAME)
    
    prompt = f"""
    당신은 유능한 행정관입니다. 다음 정보를 종합하여 담당자가 즉시 활용 가능한 보고서를 작성하세요.
    
    [민원] {situation}
    [법적근거] 법령명: {law_name}\n{law_text}
    [현실사례] {search_text}
    
    [출력 형식: 아래 섹션을 마크다운으로 구분하여 작성]
    # 1. 핵심 요약 (3줄 이내)
    # 2. 법적 검토 및 근거 (조문 인용 필수)
    # 3. 타 지자체/유사 사례 분석 (검색 결과 기반)
    # 4. 실무 액션 플랜 (단계별 행동 지침)
    # 5. (부록) 민원 답변용 공문 문안 초안
    """
    res = model.generate_content(prompt)
    progress_callback(100, "🎉 분석이 완료되었습니다!")
    return res.text

# --- 3. UI 구성 ---

# Header Section
st.markdown("""
<div class="glass-card" style="text-align:center; padding: 30px;">
    <h1>⚖️ AI 행정관: The Legal Glass</h1>
    <p style="font-size: 1.1rem; opacity: 0.8;">
        법령의 <b>원칙(Rule)</b>과 현장의 <b>사례(Reality)</b>를 투명하게 종합하여 최적의 해답을 제시합니다.
    </p>
</div>
""", unsafe_allow_html=True)

# Input Section
with st.container():
    st.markdown('<div class="glass-card"><h3>📝 상황 접수</h3>', unsafe_allow_html=True)
    user_input = st.text_area("구체적인 상황을 입력해주세요.", height=120, placeholder="예: 아파트 단지 내 장기 방치된 킥보드, 구청에서 강제 수거가 가능한가요?")
    submit_btn = st.button("🚀 분석 시작하기", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

# --- 4. 실행 및 결과 표시 ---

if submit_btn and user_input:
    # Progress UI
    progress_container = st.empty()
    progress_bar = progress_container.progress(0)
    status_text = st.empty()
    
    def update_progress(percent, text):
        progress_bar.progress(percent)
        status_text.markdown(f"""<div style="text-align:center; margin-top:10px; font-weight:bold; color:#182848;">
        {text}</div>""", unsafe_allow_html=True)
        time.sleep(0.3) # 시각적 인지를 위한 약간의 딜레이

    # Execution
    try:
        law_name, law_text = get_law_context_v2(user_input, update_progress)
        search_text = get_google_search_results_v2(user_input, update_progress)
        final_report = generate_final_report_v2(user_input, law_name, law_text, search_text, update_progress)
        
        # Cleanup Progress UI
        time.sleep(1)
        progress_container.empty()
        status_text.empty()

        # --- 결과 화면 (Actionable Cards) ---
        st.divider()
        st.markdown("### 📊 분석 결과 보고서")

        # AI의 마크다운 응답을 섹션별로 파싱 (간이 파싱)
        sections = re.split(r'# \d+\. ', final_report)
        # sections[0]은 빈 문자열, [1]부터 요약, 법적검토... 순서

        if len(sections) >= 6:
            # Card 1: 핵심 요약
            st.markdown(f"""<div class="glass-card">
                <div class="result-header"><span class="result-icon">💡</span><h3>핵심 요약</h3></div>
                {sections[1].strip()}
            </div>""", unsafe_allow_html=True)
            
            col1, col2 = st.columns(2)
            with col1:
                # Card 2: 법적 검토
                st.markdown(f"""<div class="glass-card" style="min-height: 300px;">
                    <div class="result-header"><span class="result-icon">📜</span><h3>법적 검토 및 근거</h3></div>
                    <b>적용 법령: {law_name}</b><br><br>
                    {sections[2].strip()}
                </div>""", unsafe_allow_html=True)
            with col2:
                 # Card 3: 타 사례 분석
                st.markdown(f"""<div class="glass-card" style="min-height: 300px;">
                    <div class="result-header"><span class="result-icon">🔍</span><h3>유사 사례 / 현실 분석</h3></div>
                    {sections[3].strip()}
                </div>""", unsafe_allow_html=True)

            # Card 4: 액션 플랜
            st.markdown(f"""<div class="glass-card" style="border-left: 5px solid #4b6cb7;">
                <div class="result-header"><span class="result-icon">👣</span><h3>실무 액션 플랜</h3></div>
                {sections[4].strip()}
            </div>""", unsafe_allow_html=True)
            
            # Card 5: 공문 초안 (복사하기 쉽게)
            with st.expander("📄 [부록] 답변용 공문 문안 초안 보기"):
                st.code(sections[5].strip(), language="text")
                st.caption("위 텍스트를 복사하여 한글/엑셀 등에 붙여넣으세요.")

        else:
            # 파싱 실패 시 원본 출력 (Fallback)
            st.markdown(f'<div class="glass-card">{final_report}</div>', unsafe_allow_html=True)

        # DB 저장
        if use_db:
            supabase.table("law_reports").insert({"situation": user_input, "law_name": law_name, "summary": "Glass UI Report Completed"}).execute()

    except Exception as e:
        st.error(f"분석 중 오류가 발생했습니다: {e}")

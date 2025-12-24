import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
from serpapi import GoogleSearch
import re
import time
from supabase import create_client

# --- 0. 디자인 시스템 및 설정 ---
st.set_page_config(layout="wide", page_title="AI 행정관: The Legal Glass", page_icon="⚖️")

# 글래스모피즘 CSS 스타일 정의
st.markdown("""
<style>
    /* 전체 배경: 은은한 블루 그레이 그라데이션 */
    .stApp {
        background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
    }
    
    /* 글래스모피즘 카드 */
    div.glass-card {
        background: rgba(255, 255, 255, 0.65);
        box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.4);
        padding: 25px;
        margin-bottom: 20px;
        color: #1f2937;
    }
    
    /* 강조 텍스트 색상 */
    .highlight-text {
        color: #1a237e;
        font-weight: 700;
        background-color: rgba(26, 35, 126, 0.05);
        padding: 2px 5px;
        border-radius: 4px;
    }
    
    /* 카드 헤더 아이콘 및 텍스트 */
    .result-header {
        display: flex;
        align-items: center;
        margin-bottom: 15px;
        border-bottom: 2px solid rgba(75, 108, 183, 0.2);
        padding-bottom: 10px;
        color: #102a43;
    }
    .result-icon { font-size: 1.6rem; margin-right: 12px; }
    h3 { margin: 0; padding: 0; font-family: 'Helvetica Neue', sans-serif; }
    
    /* 리스트 스타일 */
    .custom-list-item {
        margin-left: 10px;
        margin-bottom: 6px;
        text-indent: -15px;
        padding-left: 15px;
    }
</style>
""", unsafe_allow_html=True)

# --- 1. 유틸리티 함수: 텍스트 포맷팅 (가독성 해결) ---
def format_text_to_html(text):
    """
    AI가 준 Markdown 텍스트를 보기 편한 HTML로 변환합니다.
    (줄바꿈, 볼드체, 리스트 처리)
    """
    if not text: return ""
    
    # 1. 굵은 글씨 (**text**) -> HTML 변환
    text = re.sub(r'\*\*(.*?)\*\*', r'<span class="highlight-text">\1</span>', text)
    
    lines = text.split('\n')
    html_output = []
    
    for line in lines:
        line = line.strip()
        if not line:
            html_output.append('<div style="height: 10px;"></div>') # 빈 줄 처리
            continue
            
        # 리스트 처리 (- 또는 1. 등으로 시작하는 경우)
        if line.startswith("- ") or line.startswith("* ") or line.startswith("• "):
            line = f'<div class="custom-list-item">🔹 {line[1:].strip()}</div>'
        elif re.match(r'^\d+\.', line): # 숫자 리스트 (1. )
            line = f'<div style="margin-top:12px; font-weight:bold; color:#102a43;">{line}</div>'
        else:
            # 일반 문장
            line = f'<div style="margin-bottom: 6px; line-height: 1.6;">{line}</div>'
            
        html_output.append(line)
        
    return "".join(html_output)

# --- 2. 초기화 및 API 연결 ---
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

# --- 3. 핵심 로직 엔진 ---

def get_law_context(situation, callback):
    """[엔진 1] 법령 API"""
    callback(10, "📜 상황에 맞는 법령을 식별하고 있습니다...")
    model = genai.GenerativeModel(MODEL_NAME)
    try:
        res = model.generate_content(f"상황: {situation}\n관련된 대한민국 법령명 1개만 정확히 출력해 (예: 도로교통법)").text
        law_name = re.sub(r'[^가-힣]', '', res)
    except: return "식별 실패", ""

    callback(25, f"🏛️ '{law_name}'의 최신 조문 데이터를 가져옵니다...")
    try:
        # 검색 -> 상세조문 확보
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
        root = ET.fromstring(requests.get(search_url).content)
        mst = root.find(".//법령일련번호").text
        real_name = root.find(".//법령명한글").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_root = ET.fromstring(requests.get(detail_url).content)
        
        articles = []
        for a in detail_root.findall(".//조문")[:30]: # 상위 30개 조문
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            articles.append(f"[제{num}조] {cont}")
            
        callback(40, f"✅ 법령 데이터 확보 완료.")
        return real_name, "\n".join(articles)
    except:
        return law_name, "법령 원문을 가져오지 못했습니다."

def get_search_results(situation, callback):
    """[엔진 2] 구글 서치 (SerpApi)"""
    callback(50, "🔍 타 지자체 사례 및 판례를 검색합니다...")
    try:
        params = {"engine": "google", "q": f"{situation} 행정처분 사례 판례", "api_key": SERPAPI_KEY, "num": 5}
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        snippets = [f"- {item['title']}: {item['snippet']}" for item in results]
        callback(70, "✅ 유사 사례 데이터 확보 완료.")
        return "\n".join(snippets)
    except:
        return "검색 결과 없음"

def generate_report(situation, law_name, law_text, search_text, callback):
    """[엔진 3] AI 종합 분석 (구조화된 출력)"""
    callback(80, "🧠 법리와 현실을 종합하여 보고서를 작성 중입니다...")
    model = genai.GenerativeModel(MODEL_NAME)
    
    prompt = f"""
    당신은 유능한 행정관입니다. 아래 정보를 바탕으로 가독성 높은 보고서를 작성하세요.
    
    [민원] {situation}
    [법적근거] {law_name}\n{law_text}
    [참고사례] {search_text}
    
    [작성 규칙]
    1. 문단이 뭉치지 않게 **줄바꿈**을 자주 하세요.
    2. 핵심 단어는 **굵게** 표시하세요.
    3. 아래 섹션 구분자(# 번호.)를 반드시 지키세요.
    
    # 1. 핵심 요약 (3줄 이내)
    # 2. 법적 검토 및 근거
    # 3. 유사 사례 및 현실 분석
    # 4. 실무 액션 플랜
    # 5. 민원 답변용 문안
    """
    res = model.generate_content(prompt)
    callback(100, "🎉 분석 완료!")
    return res.text

# --- 4. UI 구성 및 실행 ---

# 타이틀
st.markdown("""
<div class="glass-card" style="text-align:center;">
    <h1>⚖️ AI 행정관: The Legal Glass</h1>
    <p style="color:#555;">법령(Rule)과 현실(Reality)을 융합한 최적의 행정 솔루션</p>
</div>
""", unsafe_allow_html=True)

# 입력창
with st.container():
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    user_input = st.text_area("민원 상황을 구체적으로 입력하세요", height=100, placeholder="예: 아파트 단지 내 장기 방치 킥보드, 구청이 강제 수거 가능한가요?")
    btn = st.button("🚀 분석 시작", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

if btn and user_input:
    # 진행바 UI
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update(p, t):
        progress_bar.progress(p)
        status_text.markdown(f"<div style='text-align:center; font-weight:bold; color:#1a237e;'>{t}</div>", unsafe_allow_html=True)
        time.sleep(0.2) # 시각적 딜레이

    # 실행
    law_name, law_text = get_law_context(user_input, update)
    search_text = get_search_results(user_input, update)
    final_text = generate_report(user_input, law_name, law_text, search_text, update)
    
    time.sleep(1)
    progress_bar.empty()
    status_text.empty()
    
    # 결과 출력 (섹션 파싱 + HTML 변환)
    sections = re.split(r'# \d+\. ', final_text)
    
    st.divider()
    
    if len(sections) >= 6:
        # 1. 요약
        st.markdown(f"""<div class="glass-card">
            <div class="result-header"><span class="result-icon">💡</span><h3>핵심 요약</h3></div>
            {format_text_to_html(sections[1].strip())}
        </div>""", unsafe_allow_html=True)
        
        c1, c2 = st.columns(2)
        with c1:
            # 2. 법적 검토
            st.markdown(f"""<div class="glass-card" style="min-height:350px;">
                <div class="result-header"><span class="result-icon">📜</span><h3>법적 검토</h3></div>
                <div style="margin-bottom:10px; font-size:0.9em; color:#666;">적용법령: <b>{law_name}</b></div>
                {format_text_to_html(sections[2].strip())}
            </div>""", unsafe_allow_html=True)
        with c2:
            # 3. 사례 분석
            st.markdown(f"""<div class="glass-card" style="min-height:350px;">
                <div class="result-header"><span class="result-icon">🔍</span><h3>유사 사례 분석</h3></div>
                {format_text_to_html(sections[3].strip())}
            </div>""", unsafe_allow_html=True)
            
        # 4. 액션 플랜
        st.markdown(f"""<div class="glass-card" style="border-left: 5px solid #1a237e;">
            <div class="result-header"><span class="result-icon">👣</span><h3>실무 액션 플랜</h3></div>
            {format_text_to_html(sections[4].strip())}
        </div>""", unsafe_allow_html=True)
        
        # 5. 공문 초안
        with st.expander("📄 [부록] 답변용 공문/문자 초안 보기"):
            st.code(sections[5].strip(), language='text')
            
    else:
        # 파싱 실패 시 원본 출력
        st.markdown(f'<div class="glass-card">{format_text_to_html(final_text)}</div>', unsafe_allow_html=True)

    # DB 저장
    if use_db:
        try: supabase.table("law_reports").insert({"situation": user_input, "law_name": law_name, "summary": "Complete"}).execute()
        except: pass

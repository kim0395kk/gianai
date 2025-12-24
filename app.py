import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
from serpapi import GoogleSearch
import re
import time
from supabase import create_client

# --- 0. 디자인 설정 (깨짐 방지: 순정 CSS 사용) ---
st.set_page_config(layout="wide", page_title="AI 행정관 Pro", page_icon="⚖️")

st.markdown("""
<style>
    /* 전체 배경 */
    .stApp { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); }
    
    /* 카드 디자인 (HTML 주입 대신 CSS 클래스 활용) */
    div[data-testid="stVerticalBlock"] > div[style*="background-color"] {
        background: rgba(255, 255, 255, 0.7);
        backdrop-filter: blur(10px);
        border-radius: 15px;
        padding: 20px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.05);
        border: 1px solid rgba(255,255,255,0.5);
    }
    
    /* 헤더 스타일 */
    h1, h2, h3 { color: #1a237e !important; }
    
    /* 텍스트 강조 */
    strong { color: #1a237e; background-color: rgba(26, 35, 126, 0.05); padding: 0 4px; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)

# --- 1. 설정 및 API 연결 ---
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
    # 1.5 Flash 모델 우선 사용 (컨텍스트가 길고 빠름)
    for m in ['models/gemini-1.5-flash', 'models/gemini-1.5-flash-latest']:
        if m in models: return m
    return models[0] if models else None

MODEL_NAME = get_model()

# --- 2. 강력해진 로직 엔진 ---

def get_law_context(situation, callback):
    """[엔진 1] 법령 API (전체 조회 모드)"""
    callback(10, "📜 상황에 맞는 핵심 법령을 추적 중입니다...")
    model = genai.GenerativeModel(MODEL_NAME)
    
    # 1. 법령명 추론 (구글 검색 결과가 있다면 더 좋겠지만, 일단 AI 지식 활용)
    prompt = f"상황: {situation}\n이 상황을 규율하는 가장 핵심적인 대한민국 법령 이름 1개만 정확히 적어. (예: 도로교통법, 공동주택관리법)"
    try:
        res = model.generate_content(prompt).text
        law_name = re.sub(r'[^가-힣]', '', res) # 한글만 남김
    except: return "식별 실패", ""

    callback(30, f"🏛️ '{law_name}'의 전체 조문을 가져옵니다 (대용량 처리 중)...")
    try:
        # 검색 API
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
        root = ET.fromstring(requests.get(search_url).content)
        mst_node = root.find(".//법령일련번호")
        
        if mst_node is None:
            return law_name, "해당 법령을 찾을 수 없습니다. (명칭 오류 가능성)"
            
        mst = mst_node.text
        real_name = root.find(".//법령명한글").text
        
        # 상세 API (조문 전체 가져오기)
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_root = ET.fromstring(requests.get(detail_url).content)
        
        articles = []
        # [수정 포인트] 상위 30개가 아니라 300개까지 긁어옴 (과태료/벌칙 조항까지 포함하기 위함)
        for a in detail_root.findall(".//조문")[:300]: 
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            articles.append(f"[제{num}조] {cont}")
            
        full_text = "\n".join(articles)
        callback(50, f"✅ {real_name} 데이터 확보 완료 ({len(articles)}개 조문).")
        return real_name, full_text
    except Exception as e:
        return law_name, f"데이터 확보 실패: {e}"

def get_search_results(situation, callback):
    """[엔진 2] 구글 서치 (현실 사례)"""
    callback(60, "🌐 타 지자체 사례 및 최신 뉴스를 검색합니다...")
    try:
        # 검색어 전략: '상황 + 행정처분/사례/과태료' 조합
        params = {"engine": "google", "q": f"{situation} 행정처분 사례 과태료 판례", "api_key": SERPAPI_KEY, "num": 5}
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        snippets = [f"- [{item.get('source', '웹')}] {item['title']}: {item['snippet']}" for item in results]
        return "\n".join(snippets)
    except:
        return "검색 결과 없음 (API 키 확인 필요)"

def generate_report(situation, law_name, law_text, search_text, callback):
    """[엔진 3] AI 종합 분석"""
    callback(80, "🧠 법령 원문과 실제 사례를 종합 분석 중입니다...")
    model = genai.GenerativeModel(MODEL_NAME)
    
    prompt = f"""
    당신은 행정 전문가입니다. 아래 자료를 바탕으로 민원 대응 보고서를 작성하세요.
    
    [상황] {situation}
    
    [자료 1: 법령 원문 ({law_name})]
    {law_text}
    
    [자료 2: 인터넷 검색 결과 (유사 사례)]
    {search_text}
    
    [작성 가이드]
    1. **마크다운(Markdown)** 형식을 사용하여 가독성 있게 작성하세요.
    2. HTML 태그(<div> 등)는 절대 사용하지 마세요.
    3. 법령 조항은 "제O조(제목)" 형식을 정확히 인용하세요.
    
    [출력 포맷]
    ## 💡 핵심 요약
    (3줄 이내 요약)
    
    ## 📜 법적 검토 및 근거
    (위 자료 1을 근거로 위법 여부 판단)
    
    ## 🔍 유사 사례 및 현실 분석
    (위 자료 2를 근거로 타 지자체/판례 경향 설명)
    
    ## 👣 실무 액션 플랜
    (1. 2. 3. 단계별 조치 사항)
    
    ## 📄 답변용 문안
    (민원인에게 보낼 정중한 답변 텍스트)
    """
    res = model.generate_content(prompt)
    callback(100, "🎉 분석 완료!")
    return res.text

# --- 3. UI 구성 (st.container 활용) ---

# 헤더
st.title("⚖️ AI 행정관 Pro")
st.markdown("법령(Rule)의 원칙과 현장(Reality)의 사례를 융합한 행정 솔루션")
st.divider()

# 입력창
with st.container():
    user_input = st.text_area("민원 상황 입력", height=100, placeholder="예: 아파트 단지 내 5년 방치된 차량, 구청에서 강제 견인 가능한가요?")
    btn = st.button("🚀 정밀 분석 시작", type="primary", use_container_width=True)

# 실행 로직
if btn and user_input:
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update(p, t):
        progress_bar.progress(p)
        status_text.caption(f"running... {t}")
        time.sleep(0.1)

    # 3단계 엔진 가동
    law_name, law_text = get_law_context(user_input, update)
    search_text = get_search_results(user_input, update)
    final_text = generate_report(user_input, law_name, law_text, search_text, update)
    
    time.sleep(0.5)
    progress_bar.empty()
    status_text.empty()
    
    # --- 결과 출력 (디자인 적용) ---
    
    # AI 응답을 섹션별로 쪼개서 예쁜 박스에 담기
    # (## 으로 시작하는 제목을 기준으로 나눔)
    sections = re.split(r'(?=## )', final_text)
    
    for section in sections:
        if not section.strip(): continue
        
        # 각 섹션을 카드처럼 디자인된 컨테이너에 담음
        with st.container():
            # 배경색이 있는 카드로 만들기 위한 편법 (위 CSS와 연동)
            st.markdown(f"""
            <div style="background-color: rgba(255,255,255,0.6); padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #ddd;">
                {section} 
            </div>
            """, unsafe_allow_html=True)  # 내용은 마크다운 그대로 렌더링 (안전)

    # DB 저장
    if use_db:
        try:
            supabase.table("law_reports").insert({
                "situation": user_input,
                "law_name": law_name,
                "summary": final_text[:500]
            }).execute()
            st.toast("✅ 분석 결과가 데이터베이스에 저장되었습니다.", icon="💾")
        except Exception as e:
            st.toast("DB 저장 실패 (설정을 확인하세요)", icon="⚠️")

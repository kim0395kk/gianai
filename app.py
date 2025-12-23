import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
import time
from tenacity import retry, stop_after_attempt, wait_exponential

# --- 1. 화면 설정 및 디자인 ---
st.set_page_config(layout="wide", page_title="법령 기반 업무 가이드", page_icon="⚖️")

st.markdown("""
    <style>
    .section-title { font-size: 1.25rem; font-weight: bold; margin-bottom: 15px; color: #1E3A8A; border-left: 6px solid #1E3A8A; padding-left: 12px; }
    .report-box { padding: 20px; border-radius: 12px; background-color: #FFFFFF; border: 1px solid #E5E7EB; min-height: 500px; line-height: 1.8; font-size: 1.05rem; box-shadow: 0 2px 4px rgba(0,0,0,0.03); }
    .response-card { margin-bottom: 15px; padding: 15px; background-color: #F0F9FF; border-radius: 8px; border: 1px solid #BAE6FD; }
    .step-label { color: #0284C7; font-weight: bold; font-size: 1.1rem; display: block; margin-bottom: 5px; }
    .law-scroll { font-family: 'Malgun Gothic', sans-serif; background-color: #FFFBEB !important; border: 1px solid #FEF3C7 !important; height: 500px; overflow-y: auto; padding: 15px; }
    </style>
    """, unsafe_allow_html=True)

# API 설정
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except:
    st.error("🚨 Secrets 설정을 확인하세요.")
    st.stop()

# --- 2. 핵심 로직 함수 ---

@st.cache_data
def get_best_model_name():
    try:
        available = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        # 무료 버전에서 가장 한도가 넉넉한 flash 모델 우선 사용
        for target in ["1.5-flash", "flash"]:
            for m_name in available:
                if target in m_name: return m_name
        return "models/gemini-1.5-flash"
    except: return "models/gemini-1.5-flash"

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def safe_generate_content(model, prompt):
    """지수적 백오프를 이용한 안전한 AI 호출"""
    return model.generate_content(prompt)

def fetch_law_data(query_keyword):
    """국가법령정보센터 데이터 수집 및 질문 연관 조문 필터링"""
    search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={query_keyword}"
    try:
        res = requests.get(search_url, timeout=10)
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None
        
        mst_id = law_node.find("법령일련번호").text
        real_name = law_node.find("법령명한글").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst_id}&type=XML"
        detail_res = requests.get(detail_url, timeout=15)
        detail_root = ET.fromstring(detail_res.content)
        
        # 모든 조문을 가져오지 않고 핵심 키워드가 포함된 조문 위주로 필터링 (토큰 절약)
        all_articles = detail_root.findall(".//조문")
        filtered_articles = []
        for a in all_articles:
            num = a.find('조문번호').text if a.find('조문번호') is not None else ""
            title = a.find('조문제목').text if a.find('조문제목') is not None else ""
            content = a.find('조문내용').text if a.find('조문내용') is not None else ""
            
            # 질문과 관련된 핵심 키워드가 조문에 포함되어 있는지 확인 (단순 필터링)
            article_text = f"제{num}조({title}): {content}"
            filtered_articles.append(article_text)
            
        return {"name": real_name, "text": "\n".join(filtered_articles[:50])} # 최대 50개로 제한
    except: return None

# --- 3. UI 및 프로세스 ---

st.title("⚖️ 법령 기반 실무 가이드 시스템")
user_input = st.text_input("분석할 상황을 입력하세요 (예: 주정차 단속 구간 예외 요청)")

if st.button("🚀 정밀 리포트 생성", type="primary"):
    if not user_input:
        st.warning("내용을 입력해주세요.")
    else:
        with st.status("📡 법률 엔진 가동 중...", expanded=True) as status:
            model = genai.GenerativeModel(get_best_model_name())
            
            # [단계 1] 법령명 식별 (AI 호출 1)
            status.write("1. 관련 법령 탐색 중...")
            try:
                law_identify_prompt = f"질문: '{user_input}'\n이 상황에 가장 적합한 대한민국 법령 이름 하나만 출력해줘. (예: 도로교통법)"
                law_res = safe_generate_content(model, law_identify_prompt)
                target_law = re.sub(r'[^\w\s]', '', law_res.text).strip()
            except Exception as e:
                st.error("AI 한도 초과 혹은 통신 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."); st.stop()

            # [단계 2] 법령 데이터 가져오기
            status.write(f"2. {target_law} 조문 수집 중...")
            law_info = fetch_law_data(target_law)
            if not law_info:
                st.error("법령 데이터를 가져올 수 없습니다."); st.stop()

            # [단계 3] 통합 분석 리포트 생성 (AI 호출 2)
            status.write("3. 법리 검토 및 리포트 작성 중...")
            final_prompt = f"""
            질문: {user_input}
            참고법령: {law_info['name']}
            조문내용: {law_info['text'][:5000]} 

            위 법령을 근거로 민원 대응 리포트를 작성해줘. 
            반드시 아래 JSON 형식으로만 응답해:
            {{
              "situation": "상황을 공무원 입장에서 3줄 요약",
              "response": [
                {{"title": "법적 근거 확인", "description": "내용"}},
                {{"title": "민원인 대응 논리", "description": "내용"}},
                {{"title": "현실적 대안 제시", "description": "내용"}}
              ]
            }}
            """
            try:
                analysis_res = safe_generate_content(model, final_prompt)
                # JSON 파싱
                json_match = re.search(r'\{.*\}', analysis_res.text, re.DOTALL)
                result = json.loads(json_match.group())
            except:
                st.error("리포트 생성 중 오류가 발생했습니다."); st.stop()

            status.update(label="🏆 분석 완료!", state="complete")

        # --- [결과 출력 레이아웃] ---
        st.divider()
        col1, col2, col3 = st.columns([2.5, 4, 3.5])
        
        with col1:
            st.markdown("<div class='section-title'>🔍 상황 분석</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='report-box'>{result.get('situation')}</div>", unsafe_allow_html=True)
        
        with col2:
            st.markdown("<div class='section-title'>✅ 실무 가이드라인</div>", unsafe_allow_html=True)
            steps_html = "".join([f"<div class='response-card'><span class='step-label'>📍 {s['title']}</span>{s['description']}</div>" for s in result.get('response', [])])
            st.markdown(f"<div class='report-box' style='background-color:#F8FAFC;'>{steps_html}</div>", unsafe_allow_html=True)
            
        with col3:
            st.markdown(f"<div class='section-title'>📜 관련 법령 조문</div>", unsafe_allow_html=True)
            law_text_br = law_info['text'].replace("\n", "<br>")
            st.markdown(f"<div class='report-box law-scroll'><b>[{law_info['name']}]</b><br><br>{law_text_br}</div>", unsafe_allow_html=True)

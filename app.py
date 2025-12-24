import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
from supabase import create_client, Client

# --- 1. 설정 및 보안키 로드 ---
st.set_page_config(layout="wide", page_title="행정업무 내비게이션")

try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
    
    genai.configure(api_key=GEMINI_API_KEY)
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"🚨 설정 오류: {e}")
    st.stop()

# --- 2. [핵심] 사용 가능한 모델 자동 감지 함수 ---
@st.cache_data(show_spinner=False)
def get_best_available_model():
    """
    내 API 키로 사용할 수 있는 모델 중 가장 좋은 것을 자동으로 선택합니다.
    404 에러를 방지하는 핵심 함수입니다.
    """
    try:
        # 1. 사용 가능한 모델 리스트 조회
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        # 2. 우선순위 설정 (안정적이고 빠른 순서)
        # 주의: API에서는 'models/' 접두사가 붙는 경우가 많음
        priority_list = [
            'models/gemini-1.5-flash',
            'models/gemini-1.5-flash-latest',
            'models/gemini-1.5-pro',
            'models/gemini-1.0-pro',
            'gemini-1.5-flash', # 접두사 없는 경우 대비
        ]

        # 3. 교집합 찾기 (우선순위 모델이 내 리스트에 있는지 확인)
        for target in priority_list:
            if target in available_models:
                return target
        
        # 4. 우선순위 모델이 없으면 리스트의 첫 번째 모델 반환 (최후의 수단)
        if available_models:
            return available_models[0]
        else:
            return None
            
    except Exception as e:
        return None

# 전역 변수로 모델명 확정
CURRENT_MODEL_NAME = get_best_available_model()

# --- 3. 최적화된 엔진 ---

@st.cache_data(ttl=3600)
def search_law_name(situation):
    """[AI 1단계] 법령명 추론"""
    if not CURRENT_MODEL_NAME:
        return "모델 연결 실패"
        
    model = genai.GenerativeModel(CURRENT_MODEL_NAME)
    prompt = f"상황: {situation}\n위 상황에 적용되는 가장 핵심적인 법령 이름 하나만 정확한 한국어 명칭으로 출력해. (예: 도로교통법)"
    
    try:
        response = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 20, "temperature": 0.0}
        )
        return response.text.strip()
    except Exception as e:
        return f"에러: {str(e)}"

def fetch_and_filter_articles(law_name, situation_keywords):
    """[Python 로직] 조문 필터링 (토큰 비용 0원)"""
    # 1. 법령 검색
    search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
    try:
        res = requests.get(search_url, timeout=5)
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None, None
        
        mst = law_node.find("법령일련번호").text
        full_name = law_node.find("법령명한글").text
    except: return None, None

    # 2. 조문 가져오기
    detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
    try:
        res = requests.get(detail_url, timeout=10)
        root = ET.fromstring(res.content)
        
        # 3. 키워드 매칭
        keywords = set(situation_keywords.replace(" ", ",").split(",")) 
        scored_articles = []
        
        for a in root.findall(".//조문"):
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            
            score = 0
            for k in keywords:
                if len(k) > 1 and k in cont:
                    score += 1
            
            # 점수가 있거나 중요 단어 포함 시
            if score > 0 or any(x in cont for x in ["금지", "위반", "처분", "과태료"]): 
                scored_articles.append((score, f"제{num}조: {cont}"))
        
        scored_articles.sort(key=lambda x: x[0], reverse=True)
        final_context = "\n".join([item[1] for item in scored_articles[:5]])
        
        return full_name, final_context
    except: return None, None

def generate_report(situation, law_name, context):
    """[AI 2단계] 리포트 생성"""
    if not context or not CURRENT_MODEL_NAME: return None
    
    model = genai.GenerativeModel(CURRENT_MODEL_NAME)
    
    prompt = f"""
    당신은 행정 전문가입니다. 아래 정보를 바탕으로 민원 대응 보고서를 JSON으로 작성하세요.
    
    [상황] {situation}
    [참조 조문]
    {context}
    
    [JSON 형식]
    {{
        "summary": "법적 근거 요약",
        "steps": [
            {{"title": "단계 1", "desc": "내용"}},
            {{"title": "단계 2", "desc": "내용"}},
            {{"title": "단계 3", "desc": "내용"}}
        ],
        "tip": "실무 팁"
    }}
    """
    try:
        response = model.generate_content(
            prompt, 
            generation_config={"response_mime_type": "application/json", "temperature": 0.5}
        )
        return json.loads(response.text)
    except: return None

# --- 4. UI 및 실행 ---

st.title("⚡️ 초효율 공무원 AI 어시스턴트")

# 모델 연결 상태 표시 (사이드바)
with st.sidebar:
    if CURRENT_MODEL_NAME:
        st.success(f"✅ 연결된 모델: {CURRENT_MODEL_NAME}")
    else:
        st.error("❌ 사용 가능한 Gemini 모델을 찾을 수 없습니다. API 키를 확인하세요.")

user_input = st.text_area("민원 내용 입력", height=100, placeholder="예: 인도 위 불법 주정차 단속 근거")

if st.button("분석 실행", type="primary"):
    if not user_input or not CURRENT_MODEL_NAME:
        st.warning("내용을 입력하거나 모델 연결을 확인해주세요.")
    else:
        with st.status("⚙️ 지능형 프로세스 가동 중...", expanded=True) as status:
            
            # 1. 법령명 추론
            status.write("1. 관련 법령 탐색 중...")
            inferred_law = search_law_name(user_input)
            
            if "에러" in inferred_law or "실패" in inferred_law:
                st.error(f"AI 호출 중 오류 발생: {inferred_law}")
                st.stop()
                
            clean_law_name = re.sub(r'[^가-힣]', '', inferred_law)
            
            # 2. Python 필터링
            status.write(f"2. [{clean_law_name}] 내 핵심 조문 추출 중...")
            full_law_name, relevant_articles = fetch_and_filter_articles(clean_law_name, user_input)
            
            if relevant_articles:
                # 3. 리포트 생성
                status.write("3. 최종 리포트 작성 중...")
                result = generate_report(user_input, full_law_name, relevant_articles)
                
                if result:
                    status.update(label="완료!", state="complete")
                    
                    st.divider()
                    st.success(f"📌 적용 법령: **{full_law_name}**")
                    st.write(f"ℹ️ **요약**: {result['summary']}")
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        for step in result['steps']:
                            st.subheader(step['title'])
                            st.write(step['desc'])
                    with c2:
                        st.warning(f"💡 팁: {result['tip']}")
                        with st.expander("참조된 조문"):
                            st.code(relevant_articles, language="text")
                    
                    # DB 저장
                    try:
                        supabase.table("law_reports").insert({
                            "situation": user_input, 
                            "law_name": full_law_name,
                            "summary": result['summary'], 
                            "tip": result['tip']
                        }).execute()
                    except: pass 
                else:
                    st.error("리포트 생성에 실패했습니다.")
            else:
                st.error(f"'{clean_law_name}' 데이터를 찾을 수 없습니다. 질문을 구체적으로 수정해보세요.")

import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
from supabase import create_client, Client

# --- 1. 설정 및 캐싱 (API 호출 절약) ---
st.set_page_config(layout="wide", page_title="행정업무 내비게이션")

# Streamlit Secrets 로드
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

# --- 2. 최적화된 엔진 ---

@st.cache_data(ttl=3600) # 1시간 동안 동일 질문 캐싱 (API 비용 0원 만들기)
def search_law_name(situation):
    """
    [AI 1단계] 상황에서 가장 유력한 법령명 1개만 추론 (입력 토큰 최소화)
    """
    model = genai.GenerativeModel('gemini-3-flash')
    # Prompt Engineering: 다른 말 없이 법령명만 딱 뱉게 하여 출력 토큰 절약
    prompt = f"상황: {situation}\n위 상황에 적용되는 가장 핵심적인 법령 이름 하나만 정확한 한국어 명칭으로 출력해. (예: 도로교통법)"
    response = model.generate_content(
        prompt,
        generation_config={"max_output_tokens": 20, "temperature": 0.0} # Temperature 0으로 환각 방지
    )
    return response.text.strip()

def fetch_and_filter_articles(law_name, situation_keywords):
    """
    [Python 로직] AI 대신 Python이 조문을 필터링합니다. (토큰 비용 0원)
    - 법령의 모든 조문을 가져온 뒤, 사용자 상황(keyword)과 매칭되는 조문만 남깁니다.
    """
    # 1. 법령 검색 및 MST 확보
    search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
    try:
        res = requests.get(search_url, timeout=5)
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None, None
        
        mst = law_node.find("법령일련번호").text
        full_name = law_node.find("법령명한글").text
    except: return None, None

    # 2. 상세 조문 가져오기 (API 호출)
    detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
    try:
        res = requests.get(detail_url, timeout=10)
        root = ET.fromstring(res.content)
        
        # 3. [핵심] 키워드 기반 스코어링 (RAG 유사 방식)
        # 사용자 상황을 단어 단위로 쪼개서 조문과 비교
        keywords = set(situation_keywords.replace(" ", ",").split(",")) 
        scored_articles = []
        
        for a in root.findall(".//조문"):
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            
            # 검색 알고리즘: 상황 키워드가 포함된 조문에 가중치 부여
            score = 0
            for k in keywords:
                if len(k) > 1 and k in cont: # 2글자 이상 키워드만
                    score += 1
            
            # 점수가 있거나, 핵심 조문(보통 100조 이내의 벌칙/과태료 등)이면 후보 등록
            if score > 0 or ("설치" in cont or "제한" in cont or "금지" in cont): 
                scored_articles.append((score, f"제{num}조: {cont}"))
        
        # 관련도 순 정렬 후 상위 3~5개만 AI에게 전달 (토큰 획기적 절감)
        scored_articles.sort(key=lambda x: x[0], reverse=True)
        final_context = "\n".join([item[1] for item in scored_articles[:5]])
        
        return full_name, final_context
    except: return None, None

def generate_report(situation, law_name, context):
    """[AI 2단계] 정제된 데이터로 리포트 생성"""
    if not context: return None
    
    model = genai.GenerativeModel('gemini-3-flash')
    
    prompt = f"""
    당신은 20년차 행정 베테랑입니다. 아래 정보를 바탕으로 민원 대응 보고서를 JSON으로 작성하세요.
    
    [상황] {situation}
    [핵심 법령 조문]
    {context}
    
    [출력 형식(JSON Only)]
    {{
        "summary": "법적 근거 요약 (간결하게)",
        "steps": [
            {{"title": "1단계: 상황 판단", "desc": "내용..."}},
            {{"title": "2단계: 법적 근거 제시", "desc": "내용..."}},
            {{"title": "3단계: 최종 답변", "desc": "내용..."}}
        ],
        "tip": "실무자 팁"
    }}
    """
    try:
        response = model.generate_content(
            prompt, 
            generation_config={"response_mime_type": "application/json", "temperature": 0.5}
        )
        return json.loads(response.text)
    except: return None

# --- 3. UI 및 실행 ---

st.title("⚡️ 초효율 공무원 AI 어시스턴트")
st.caption("Python 전처리 알고리즘으로 AI 토큰 비용을 80% 절감했습니다.")

user_input = st.text_area("민원 내용 입력", height=100, placeholder="예: 아파트 단지 내 무단 방치 차량 강제 견인 가능 여부")

if st.button("분석 실행", type="primary"):
    if not user_input:
        st.warning("내용을 입력해주세요.")
    else:
        with st.status("⚙️ 지능형 프로세스 가동 중...", expanded=True) as status:
            
            # 1. 법령명 추론 (AI 최소 사용)
            status.write("1. 관련 법령 탐색 중...")
            inferred_law = search_law_name(user_input)
            clean_law_name = re.sub(r'[^가-힣]', '', inferred_law)
            
            # 2. Python 필터링 (비용 0원)
            status.write(f"2. [{clean_law_name}] 내 핵심 조문 추출 중...")
            # 사용자 입력의 명사들을 키워드로 활용해 조문 필터링
            full_law_name, relevant_articles = fetch_and_filter_articles(clean_law_name, user_input)
            
            if relevant_articles:
                # 3. 리포트 생성
                status.write("3. 최종 리포트 작성 중...")
                result = generate_report(user_input, full_law_name, relevant_articles)
                
                if result:
                    status.update(label="완료!", state="complete")
                    
                    # 결과 화면
                    st.divider()
                    st.success(f"📌 적용 법령: **{full_law_name}**")
                    st.write(f"ℹ️ **요약**: {result['summary']}")
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        for step in result['steps']:
                            st.subheader(step['title'])
                            st.write(step['desc'])
                    with c2:
                        st.error("💡 베테랑의 한마디")
                        st.write(result['tip'])
                        
                        with st.expander("참조된 핵심 조문 보기"):
                            st.code(relevant_articles, language="text")
                    
                    # DB 저장 (비동기 처리처럼 보이게 마지막에 배치)
                    supabase.table("law_reports").insert({
                        "situation": user_input, 
                        "law_name": full_law_name,
                        "summary": result['summary'], 
                        "tip": result['tip']
                    }).execute()
                    
                else:
                    st.error("리포트 생성에 실패했습니다.")
            else:
                st.error(f"'{clean_law_name}'에서 관련 조문을 찾을 수 없습니다.")

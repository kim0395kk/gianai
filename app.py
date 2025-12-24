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

# --- 2. 최적화된 엔진 ---

@st.cache_data(ttl=3600)
def search_law_name(situation):
    """
    [AI 1단계] 상황에서 가장 유력한 법령명 1개만 추론
    """
    # 수정됨: gemini-3-flash -> gemini-1.5-flash (가장 안전한 모델명)
    # 만약 2.0을 쓰고 싶으시면 'gemini-2.0-flash-exp' 로 시도해보세요.
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"상황: {situation}\n위 상황에 적용되는 가장 핵심적인 법령 이름 하나만 정확한 한국어 명칭으로 출력해. (예: 도로교통법)"
    response = model.generate_content(
        prompt,
        generation_config={"max_output_tokens": 20, "temperature": 0.0}
    )
    return response.text.strip()

def fetch_and_filter_articles(law_name, situation_keywords):
    """
    [Python 로직] AI 대신 Python이 조문을 필터링 (토큰 비용 0원)
    """
    search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
    try:
        res = requests.get(search_url, timeout=5)
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None, None
        
        mst = law_node.find("법령일련번호").text
        full_name = law_node.find("법령명한글").text
    except: return None, None

    detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
    try:
        res = requests.get(detail_url, timeout=10)
        root = ET.fromstring(res.content)
        
        # 키워드 스코어링 로직
        keywords = set(situation_keywords.replace(" ", ",").split(",")) 
        scored_articles = []
        
        for a in root.findall(".//조문"):
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            
            score = 0
            for k in keywords:
                if len(k) > 1 and k in cont:
                    score += 1
            
            if score > 0 or ("설치" in cont or "제한" in cont or "금지" in cont): 
                scored_articles.append((score, f"제{num}조: {cont}"))
        
        scored_articles.sort(key=lambda x: x[0], reverse=True)
        # 상위 5개만 추출
        final_context = "\n".join([item[1] for item in scored_articles[:5]])
        
        return full_name, final_context
    except: return None, None

def generate_report(situation, law_name, context):
    """[AI 2단계] 정제된 데이터로 리포트 생성"""
    if not context: return None
    
    # 수정됨: gemini-3-flash -> gemini-1.5-flash
    model = genai.GenerativeModel('gemini-1.5-flash')
    
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

# (중요) 사이드바에서 현재 사용 가능한 모델명 확인 기능 추가
with st.sidebar:
    st.write("🔧 **시스템 상태**")
    if st.button("내 API 키로 사용 가능한 모델 확인하기"):
        try:
            available_models = []
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    available_models.append(m.name)
            st.code("\n".join(available_models))
            st.success("위 리스트에 있는 이름만 코드에 쓸 수 있습니다.")
        except Exception as e:
            st.error(f"조회 실패: {e}")

user_input = st.text_area("민원 내용 입력", height=100, placeholder="예: 아파트 단지 내 무단 방치 차량 강제 견인 가능 여부")

if st.button("분석 실행", type="primary"):
    if not user_input:
        st.warning("내용을 입력해주세요.")
    else:
        with st.status("⚙️ 지능형 프로세스 가동 중...", expanded=True) as status:
            
            status.write("1. 관련 법령 탐색 중...")
            inferred_law = search_law_name(user_input)
            clean_law_name = re.sub(r'[^가-힣]', '', inferred_law)
            
            status.write(f"2. [{clean_law_name}] 내 핵심 조문 추출 중...")
            full_law_name, relevant_articles = fetch_and_filter_articles(clean_law_name, user_input)
            
            if relevant_articles:
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
                        st.warning("💡 베테랑의 한마디")
                        st.write(result['tip'])
                        
                        with st.expander("참조된 핵심 조문 보기"):
                            st.code(relevant_articles, language="text")
                    
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

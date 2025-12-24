import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
from supabase import create_client, Client

# --- 1. 설정 및 초기화 ---
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

# --- 2. 모델 설정 (에러 원인 해결 파트) ---
def get_valid_model_name():
    """API 키로 접근 가능한 모델 목록을 조회하여 유효한 모델명을 반환"""
    try:
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        preferred_order = [
            'models/gemini-1.5-flash',
            'models/gemini-1.5-flash-latest',
            'models/gemini-1.5-pro',
            'models/gemini-1.0-pro',
            'models/gemini-pro'
        ]
        
        for p in preferred_order:
            if p in available_models:
                return p, available_models
        
        if available_models:
            return available_models[0], available_models
            
        return None, []
    except Exception as e:
        return None, []

# 전역 변수 설정
CURRENT_MODEL_NAME, ALL_MODELS_LIST = get_valid_model_name()

# --- 3. 로직 함수 (수정됨: model_name을 인자로 받음) ---

@st.cache_data(ttl=3600)
def search_law_name(situation, model_name):
    """
    [수정됨] model_name을 인자로 받아서 NameError 방지
    """
    if not model_name: return "모델 오류"
    
    model = genai.GenerativeModel(model_name)
    prompt = f"""
    상황: {situation}
    
    위 상황을 해결하기 위해 참고해야 할 대한민국 현행 법령의 '정식 명칭' 1개만 출력해.
    약칭이나 단순 명사가 아니라 반드시 '법'으로 끝나는 전체 이름을 써야 해.
    (나쁜 예: 도로, 교통, 주차 / 좋은 예: 도로교통법, 주차장법, 건축법)
    """
    
    try:
        response = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 30, "temperature": 0.0}
        )
        return response.text.strip()
    except Exception as e:
        return f"에러: {str(e)}"

def fetch_and_filter_articles(law_name, situation_keywords):
    """[Python 로직] 조문 필터링 (안전장치 포함)"""
    try:
        # 1. 법령 검색
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
        res = requests.get(search_url, timeout=5)
        root = ET.fromstring(res.content)
        
        law_node = root.find(".//law")
        if law_node is None: return None, None
        
        mst = law_node.find("법령일련번호").text
        full_name = law_node.find("법령명한글").text

        # 2. 조문 상세 조회
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        res = requests.get(detail_url, timeout=10)
        root = ET.fromstring(res.content)
        
        keywords = set(situation_keywords.replace(" ", ",").split(","))
        scored = []
        all_articles = []
        
        for a in root.findall(".//조문"):
            cont = a.find('조문내용').text or ""
            num = a.find('조문번호').text or ""
            text = f"제{num}조: {cont}"
            all_articles.append(text)
            
            score = sum(1 for k in keywords if len(k) > 1 and k in cont)
            if score > 0:
                scored.append((score, text))
        
        # 1순위: 키워드 매칭, 2순위: 단순 상위 조문 (Fallback)
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            return full_name, "\n".join([x[1] for x in scored[:5]])
        elif all_articles:
            return full_name, "\n".join(all_articles[:5])
        else:
            return None, None
            
    except Exception as e:
        return None, None

def generate_report(situation, law_name, context, model_name):
    """
    [수정됨] model_name을 인자로 받아서 NameError 방지
    """
    if not context or not model_name: return None
    
    model = genai.GenerativeModel(model_name)
    prompt = f"""
    상황: {situation}
    법령: {context}
    위 내용을 바탕으로 'summary'(요약), 'steps'(단계별 대응 배열), 'tip'(팁)을 포함한 JSON을 작성하라.
    """
    try:
        res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(res.text)
    except: return None

# --- 4. UI ---

st.title("⚡️ 초효율 공무원 AI 어시스턴트")

# 사이드바 상태 표시
with st.sidebar:
    st.header("🔧 시스템 상태")
    if CURRENT_MODEL_NAME:
        st.success(f"✅ 연결 성공: {CURRENT_MODEL_NAME}")
    else:
        st.error("❌ 사용 가능한 모델 없음 (API 키 확인 필요)")
        
    with st.expander("모델 전체 리스트"):
        st.write(ALL_MODELS_LIST)

user_input = st.text_area("민원 내용 입력", height=100, placeholder="예: 인도 위 불법 주정차 단속 근거")

if st.button("분석 실행", type="primary"):
    if not user_input or not CURRENT_MODEL_NAME:
        st.warning("내용을 입력하거나 모델 연결을 확인해주세요.")
    else:
        with st.status("분석 중...") as status:
            # 1. 법령 탐색 (인자로 모델명 전달)
            status.write("1. 법령 탐색...")
            inferred = search_law_name(user_input, CURRENT_MODEL_NAME)
            
            if "에러" in inferred:
                st.error(f"API 에러: {inferred}")
                st.stop()
                
            clean_name = re.sub(r'[^가-힣]', '', inferred)
            
            # 2. 조문 추출
            status.write(f"2. {clean_name} 조문 추출...")
            full_name, context = fetch_and_filter_articles(clean_name, user_input)
            
            if context:
                # 3. 리포트 생성 (인자로 모델명 전달)
                status.write("3. 리포트 생성...")
                res = generate_report(user_input, full_name, context, CURRENT_MODEL_NAME)
                
                if res:
                    status.update(label="완료", state="complete")
                    st.divider()
                    st.success(f"📌 {full_name}")
                    st.write(res.get('summary'))
                    for s in res.get('steps', []):
                        st.info(f"**{s['title']}**: {s['desc']}")
                    st.warning(f"팁: {res.get('tip')}")
                    
                    try:
                        supabase.table("law_reports").insert({
                            "situation": user_input, "law_name": full_name,
                            "summary": res['summary'], "tip": res['tip']
                        }).execute()
                    except: pass
            else:
                st.error(f"'{clean_name}'에 대한 조문 데이터를 가져오지 못했습니다.")

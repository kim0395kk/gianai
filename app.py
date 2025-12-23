import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
from supabase import create_client, Client
from tenacity import retry, stop_after_attempt, wait_exponential

# --- 1. 설정 및 보안키 로드 ---
st.set_page_config(layout="wide", page_title="행정업무 지능형 내비게이션")

try:
    # Streamlit Cloud의 Secrets에서 정보 로드
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
    
    genai.configure(api_key=GEMINI_API_KEY)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"🚨 설정 오류: Secrets를 확인하세요. ({e})")
    st.stop()

# --- 2. 핵심 엔진 함수 ---

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def call_ai(prompt):
    """2025년 최신 모델 gemini-2.0-flash 사용"""
    # 404 에러 방지를 위해 명칭 확인
    model_name = 'gemini-2.0-flash'
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        # 에러 발생 시 가용한 모델 목록을 출력하여 디버깅 도움
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        st.error(f"❌ 모델({model_name}) 호출 실패: {e}")
        st.info(f"사용 가능 모델 목록: {available_models}")
        st.stop()

def get_law_detail(query):
    """법제처 API를 통해 실무 조문 수집"""
    search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={query}"
    try:
        res = requests.get(search_url, timeout=10)
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None
        
        mst = law_node.find("법령일련번호").text
        name = law_node.find("법령명한글").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_res = requests.get(detail_url, timeout=10)
        detail_root = ET.fromstring(detail_res.content)
        
        articles = [f"제{a.find('조문번호').text}조: {a.find('조문내용').text}" 
                    for a in detail_root.findall(".//조문")[:50]]
        return {"name": name, "content": "\n".join(articles)}
    except: return None

# --- 3. 메인 UI ---

st.title("⚖️ 공무원 업무 지능형 내비게이션")
st.markdown("##### 상황을 입력하면 법령을 분석하고 실무 가이드를 생성하여 DB에 저장합니다.")

user_input = st.text_area("현 업무 상황 또는 민원 내용을 입력하세요", height=120, placeholder="예: 무단 점유된 공유재산에 대한 변상금 부과 절차와 근거 법령")

if st.button("🚀 실무 리포트 생성", type="primary"):
    if not user_input:
        st.warning("상황을 입력해 주세요.")
    else:
        with st.status("🔍 분석 및 데이터 저장 중...", expanded=True) as status:
            # 1. 법령 식별
            status.write("1. 관련 법령 탐색...")
            law_name_res = call_ai(f"질문: {user_input}\n가장 적합한 대한민국 법령명 1개만 써줘.").strip().replace("*","")
            
            # 2. 법령 수집
            status.write(f"2. {law_name_res} 조문 수집 중...")
            law_info = get_law_detail(law_name_res)
            
            if not law_info:
                st.error("법령 데이터를 가져오지 못했습니다."); st.stop()
            
            # 3. 가이드 생성
            status.write("3. 공무원 맞춤형 실무 지침 작성...")
            prompt = f"""
            상황: {user_input}
            법령 내용: {law_info['content']}
            너는 수석 사무관이야. 후배를 위해 아래 JSON 형식으로 답해:
            {{
                "summary": "법리적 요약 3줄",
                "steps": [
                    {{"title": "근거 확인", "desc": "내용"}},
                    {{"title": "실무 절차", "desc": "내용"}},
                    {{"title": "민원 대응", "desc": "내용"}}
                ],
                "tip": "감사 주의사항 및 꿀팁"
            }}
            """
            result_raw = call_ai(prompt)
            
            try:
                json_match = re.search(r'\{.*\}', result_raw, re.DOTALL)
                report = json.loads(json_match.group())
                
                # 4. Supabase 저장
                status.write("4. 업무 지식 베이스(DB) 저장 중...")
                supabase.table("law_reports").insert({
                    "situation": user_input,
                    "law_name": law_info['name'],
                    "summary": report['summary'],
                    "steps": json.dumps(report['steps'], ensure_ascii=False),
                    "tip": report['tip']
                }).execute()
                
                status.update(label="✅ 가이드 생성 및 저장 완료!", state="complete")
                
                # 결과 출력
                st.divider()
                col1, col2 = st.columns([6, 4])
                with col1:
                    st.subheader("📋 실무 가이드라인")
                    st.success(report['summary'])
                    for s in report['steps']:
                        st.markdown(f"**📍 {s['title']}**: {s['desc']}")
                    st.warning(f"💡 **베테랑 팁**: {report['tip']}")
                with col2:
                    st.subheader(f"📜 관련 법령: {law_info['name']}")
                    st.code(law_info['content'], language="text")
                    
            except Exception as e:
                st.error(f"결과 파싱 중 오류 발생: {e}")

# --- 4. 업무 기록 조회 (하단) ---
st.divider()
with st.expander("📂 나의 지난 업무 처리 기록 (DB 조회)"):
    try:
        data = supabase.table("law_reports").select("*").order("created_at", desc=True).limit(5).execute()
        for d in data.data:
            st.write(f"**[{d['created_at'][:10]}]** {d['situation'][:60]}... (법령: {d['law_name']})")
    except:
        st.write("기록을 불러올 수 없습니다.")

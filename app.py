import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
from supabase import create_client, Client
from tenacity import retry, stop_after_attempt, wait_exponential

# --- 1. 기본 설정 및 비밀키 로드 ---
st.set_page_config(layout="wide", page_title="공무원 업무 내비게이션", page_icon="⚖️")

# CSS 스타일: 공무원 업무 보고서 느낌의 깔끔한 디자인
st.markdown("""
    <style>
    .main-header { font-size: 2.2rem; font-weight: 700; color: #1e3a8a; margin-bottom: 1rem; }
    .sub-header { font-size: 1.3rem; font-weight: 600; color: #374151; margin-top: 2rem; border-left: 5px solid #1e3a8a; padding-left: 10px; }
    .report-card { background-color: white; padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e5e7eb; margin-bottom: 15px; }
    .law-box { background-color: #fefce8; padding: 15px; border-radius: 8px; border: 1px solid #fef08a; height: 500px; overflow-y: auto; font-family: 'Malgun Gothic', sans-serif; font-size: 0.95rem; }
    .step-badge { background-color: #dbeafe; color: #1e40af; padding: 4px 8px; border-radius: 4px; font-weight: bold; margin-right: 8px; }
    .tip-box { background-color: #ecfdf5; border-left: 4px solid #10b981; padding: 15px; color: #065f46; margin-top: 15px; }
    </style>
""", unsafe_allow_html=True)

# Secrets 연결 (오류 발생 시 안내)
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
    
    genai.configure(api_key=GEMINI_API_KEY)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"🚨 설정 오류: Secrets 파일에 API 키가 누락되었습니다. ({e})")
    st.stop()

# --- 2. 핵심 로직 (AI 및 법령 API) ---

# Gemini 호출 함수 (재시도 로직 및 모델명 고정)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def call_gemini(prompt):
    # 최신 무료/고성능 모델 사용 (화면에서 확인한 ID 적용)
    # 속도와 가성비가 좋은 'gemini-1.5-flash'를 기본으로 사용
    model = genai.GenerativeModel('gemini-1.5-flash') 
    response = model.generate_content(prompt)
    return response.text

# 법령 데이터 가져오기 (토큰 절약 필터링 적용)
def get_law_data(situation_keyword):
    # 1. 검색 (법령 목록)
    search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={situation_keyword}"
    try:
        res = requests.get(search_url, timeout=10)
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        
        if law_node is None: return None
        
        mst_id = law_node.find("법령일련번호").text
        real_name = law_node.find("법령명한글").text
        
        # 2. 상세 조회 (본문)
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst_id}&type=XML"
        detail_res = requests.get(detail_url, timeout=15)
        detail_root = ET.fromstring(detail_res.content)
        
        # 3. 조문 필터링 (상위 50개만 추출하여 AI에게 전달)
        articles = []
        for a in detail_root.findall(".//조문"):
            num = a.find('조문번호').text if a.find('조문번호') is not None else ""
            content = a.find('조문내용').text if a.find('조문내용') is not None else ""
            if len(content) > 5: # 너무 짧은 조항 제외
                articles.append(f"제{num}조: {content}")
        
        return {"name": real_name, "content": "\n".join(articles[:50])}
        
    except Exception as e:
        st.sidebar.error(f"법령 API 통신 중 오류: {e}")
        return None

# --- 3. 메인 UI 구성 ---

st.markdown("<div class='main-header'>⚖️ 지능형 행정업무 내비게이션</div>", unsafe_allow_html=True)
st.markdown("부서를 이동해도 걱정 마세요. **상황**만 입력하면 **법적 근거**와 **실무 가이드**를 찾아 드립니다.")

# 입력 폼
with st.form("query_form"):
    user_input = st.text_area("어떤 상황인가요?", height=100, 
                             placeholder="예) 학교 정문 앞 문방구에서 불량식품을 파는데 단속 근거가 있는지, 어떤 절차로 처리해야 하는지 궁금합니다.")
    submitted = st.form_submit_button("🚀 실무 가이드 생성하기", type="primary")

if submitted and user_input:
    col1, col2 = st.columns([6, 4]) # 결과 화면 분할
    
    with st.status("📡 법률 엔진 가동 중...", expanded=True) as status:
        # Step 1: 상황에서 법령 키워드 추출
        status.write("1. 상황 분석 및 관련 법령 탐색...")
        keyword_prompt = f"질문: '{user_input}'\n이 상황을 해결하기 위해 찾아야 할 대한민국 법령 이름 딱 1개만 알려줘. (설명 없이 법령명만 출력)"
        target_law_name = call_gemini(keyword_prompt).strip().replace("\n", "").replace("*", "")
        
        # Step 2: 법령 데이터 수집
        status.write(f"2. [{target_law_name}] 조문 데이터 수집 중...")
        law_data = get_law_data(target_law_name)
        
        if not law_data:
            st.error(f"'{target_law_name}' 관련 법령 정보를 찾을 수 없습니다. 키워드를 구체적으로 입력해 보세요.")
            st.stop()
            
        # Step 3: 실무 가이드 생성 (RAG)
        status.write("3. 법리 해석 및 실무 가이드 작성 중...")
        
        # 공무원 페르소나 프롬프트
        final_prompt = f"""
        너는 20년 차 베테랑 공무원(행정 사무관)이야. 
        후배 공무원이 아래 상황에 대해 물어봤어. 수집된 법령을 근거로 실무 가이드를 작성해줘.

        [상황]: {user_input}
        [참고 법령]: {law_data['content']}

        반드시 아래 JSON 형식으로만 답변해 (마크다운 코드블럭 쓰지 마):
        {{
            "summary": "핵심 요약 (3줄 이내)",
            "action_plan": [
                {{"step": "1. 사실 조사", "detail": "현장에서 확인해야 할 구체적 사항"}},
                {{"step": "2. 법적 검토", "detail": "적용되는 조항과 위반 여부 판단 기준"}},
                {{"step": "3. 처분/대응", "detail": "계도, 과태료 부과 등 행정 조치 절차"}}
            ],
            "admin_tip": "민원 발생을 줄이기 위한 팁이나 내부 보고서 작성 시 주의할 점"
        }}
        """
        
        result_text = call_gemini(final_prompt)
        
        try:
            # JSON 파싱 (AI가 가끔 마크다운을 섞어 쓸 때를 대비한 정제)
            cleaned_json = re.sub(r'```json|```', '', result_text).strip()
            report = json.loads(cleaned_json)
            
            # Step 4: Supabase에 기록 저장 (지식 자산화)
            status.write("4. 내 업무 기록 저장 중...")
            supabase.table("law_reports").insert({
                "situation": user_input,
                "law_name": law_data['name'],
                "summary": report['summary'],
                "steps": json.dumps(report['action_plan'], ensure_ascii=False),
                "tip": report['admin_tip']
            }).execute()
            
            status.update(label="✅ 분석 완료!", state="complete")
            
        except Exception as e:
            st.error("결과를 분석하는 도중 오류가 발생했습니다. 다시 시도해 주세요.")
            st.error(f"에러 상세: {e}")
            st.stop()

    # --- 결과 출력 ---
    st.divider()
    
    # 왼쪽: 실무 가이드 리포트
    with col1:
        st.markdown(f"<div class='sub-header'>📋 실무 대응 리포트</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='report-card'><b>📌 핵심 요약</b><br>{report['summary']}</div>", unsafe_allow_html=True)
        
        st.write("#### 👣 단계별 조치 사항")
        for plan in report['action_plan']:
            st.markdown(f"""
            <div class='report-card' style='padding: 15px; margin-bottom: 10px;'>
                <span class='step-badge'>{plan['step']}</span> {plan['detail']}
            </div>
            """, unsafe_allow_html=True)
            
        st.markdown(f"<div class='tip-box'>💡 <b>베테랑의 팁:</b> {report['admin_tip']}</div>", unsafe_allow_html=True)

    # 오른쪽: 법적 근거 (조문 원문)
    with col2:
        st.markdown(f"<div class='sub-header'>📜 법적 근거 ({law_data['name']})</div>", unsafe_allow_html=True)
        # 가독성을 위해 줄바꿈 처리
        formatted_law = law_data['content'].replace("\n", "<br><br>")
        st.markdown(f"<div class='law-box'>{formatted_law}</div>", unsafe_allow_html=True)

# --- 4. 하단: 내 업무 히스토리 (Supabase 연동 확인용) ---
st.divider()
with st.expander("🗄️ 나의 업무 처리 기록 보기 (DB 연동)"):
    try:
        # 최근 5개 기록 조회
        history = supabase.table("law_reports").select("*").order("created_at", desc=True).limit(5).execute()
        if history.data:
            for item in history.data:
                st.markdown(f"**[{item['created_at'][:10]}]** {item['situation'][:50]}... (법령: {item['law_name']})")
        else:
            st.info("아직 저장된 업무 기록이 없습니다.")
    except Exception as e:
        st.warning("DB 조회 실패 (Supabase 설정을 확인하세요)")

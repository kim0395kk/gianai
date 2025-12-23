import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
from supabase import create_client, Client

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

# --- 2. 핵심 엔진 함수 (Gemini 2.0 최적화) ---

def call_ai(prompt):
    """2025년 기준 가장 안정적인 Gemini 2.0 및 최신 모델 명칭으로 수정"""
    model_priority = [
        'gemini-2.0-flash',             # 1순위: 현재 가장 안정적인 2.0 모델
        'gemini-2.0-flash-lite-preview-02-05', # 2순위: 최신 라이트 버전
        'gemini-2.0-pro-exp',           # 3순위: 프로 버전 (이름을 짧게 수정)
        'gemini-1.5-flash',             # 4순위: (보험용) 1.5 버전이 남아있다면 작동함
    ]
    
    last_error = None
    for m_name in model_priority:
        try:
            model = genai.GenerativeModel(m_name)
            # 안전 설정은 그대로 유지
            safety = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
            ]
            response = model.generate_content(prompt, safety_settings=safety)
            if response and response.text:
                return response.text
        except Exception as e:
            last_error = str(e)
            # 404 에러나 지원하지 않는 모델 에러일 경우 즉시 다음 모델로 패스
            continue
            
    st.error(f"❌ 모든 모델 호출 실패. API 키 권한이나 모델명을 확인하세요.")
    st.info(f"마지막 발생 에러: {last_error}")
    st.stop()

def get_law_detail(query):
    """법제처 API를 통해 실무 조문 수집 (검색 및 상세 정보 통합)"""
    search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={query}"
    try:
        # 1. 법령 목록에서 MST(일련번호) 추출
        res = requests.get(search_url, timeout=10)
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None
        
        mst = law_node.find("법령일련번호").text
        name = law_node.find("법령명한글").text
        
        # 2. 해당 MST로 상세 조문 50개 가져오기
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_res = requests.get(detail_url, timeout=15)
        detail_root = ET.fromstring(detail_res.content)
        
        articles = []
        for a in detail_root.findall(".//조문"):
            num = a.find('조문번호').text if a.find('조문번호') is not None else ""
            cont = a.find('조문내용').text if a.find('조문내용') is not None else ""
            if cont:
                articles.append(f"제{num}조: {cont.strip()}")
        
        return {"name": name, "content": "\n".join(articles[:50])}
    except Exception as e:
        return None

# --- 3. 메인 UI ---

st.title("⚖️ 공무원 업무 지능형 내비게이션")
st.info("💡 본 시스템은 최신 Gemini 2.0 AI와 대한민국 법령 데이터를 실시간 연동합니다.")

user_input = st.text_area("현 업무 상황 또는 민원 내용을 입력하세요", height=150, 
                          placeholder="예: 초등학교 정문 앞 무인 단속 카메라 설치 반대 민원에 대한 대응 근거")

if st.button("🚀 실무 리포트 생성 및 DB 저장", type="primary"):
    if not user_input:
        st.warning("상황을 입력해 주세요.")
    else:
        with st.status("🔍 법령 분석 중...", expanded=True) as status:
            # Step 1: 관련 법령명 식별
            status.write("1. 관련 법령 탐색 중...")
            id_prompt = f"상황: {user_input}\n위 상황에 적용할 수 있는 가장 핵심적인 대한민국 법령 명칭 '하나'만 딱 이름만 출력해. 다른 말은 절대 하지마."
            raw_name = call_ai(id_prompt)
            law_name_cleaned = re.sub(r'[^가-힣0-9]', '', raw_name).strip() # 한글/숫자만 남김
            
            # Step 2: 법령 조문 수집
            status.write(f"2. {law_name_cleaned} 조문 수집 중...")
            law_info = get_law_detail(law_name_cleaned)
            
            if not law_info:
                st.error(f"'{law_name_cleaned}' 데이터를 가져오지 못했습니다. 법령명을 구체적으로 입력해 보세요."); st.stop()
            
            # Step 3: 가이드 생성 (JSON 포맷 강제)
            status.write("3. 수석 사무관 AI의 가이드라인 작성...")
            guide_prompt = f"""
            상황: {user_input}
            참조법령: {law_info['content']}
            
            너는 대한민국 최고의 수석 사무관이야. 후배를 위해 아래 JSON 형식으로만 답변해.
            {{
                "summary": "법리적 요약 (3줄 이내)",
                "steps": [
                    {{"title": "단계별 대응 1", "desc": "상세 내용"}},
                    {{"title": "단계별 대응 2", "desc": "상세 내용"}},
                    {{"title": "단계별 대응 3", "desc": "상세 내용"}}
                ],
                "tip": "감사 대비 및 민원 응대 꿀팁"
            }}
            """
            guide_raw = call_ai(guide_prompt)
            
            # JSON 추출 및 파싱
            try:
                json_str = re.search(r'\{.*\}', guide_raw, re.DOTALL).group()
                report = json.loads(json_str)
                
                # Step 4: Supabase 저장
                status.write("4. 지식 베이스(DB) 저장...")
                supabase.table("law_reports").insert({
                    "situation": user_input,
                    "law_name": law_info['name'],
                    "summary": report['summary'],
                    "steps": json.dumps(report['steps'], ensure_ascii=False),
                    "tip": report['tip']
                }).execute()
                
                status.update(label="✅ 분석 및 저장 완료!", state="complete")
                
                # --- 결과 출력 UI ---
                st.divider()
                res_col1, res_col2 = st.columns([7, 3])
                
                with res_col1:
                    st.subheader("📋 실무 가이드라인")
                    st.success(f"**[요약]** {report['summary']}")
                    for s in report['steps']:
                        with st.expander(f"📍 {s['title']}", expanded=True):
                            st.write(s['desc'])
                    st.warning(f"💡 **베테랑 팁**: {report['tip']}")
                
                with res_col2:
                    st.subheader("📜 근거 법령")
                    st.caption(law_info['name'])
                    st.code(law_info['content'], language="text")
                    
            except Exception as e:
                st.error(f"데이터 처리 중 오류 발생: {e}")
                st.expander("AI 응답 원문 보기").write(guide_raw)

# --- 4. 하단 기록 조회 ---
st.divider()
with st.expander("📂 최근 업무 처리 기록 (DB 연동)"):
    try:
        history = supabase.table("law_reports").select("*").order("created_at", desc=True).limit(5).execute()
        if history.data:
            for item in history.data:
                st.write(f"- **[{item['created_at'][:10]}]** {item['law_name']} | {item['situation'][:40]}...")
        else:
            st.write("저장된 기록이 없습니다.")
    except:
        st.write("DB 연결 상태를 확인해 주세요.")


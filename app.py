import streamlit as st
import google.generativeai as genai
import requests
import xml.etree.ElementTree as ET
from serpapi import GoogleSearch
import json
import re
import time
from datetime import datetime
from supabase import create_client

# --- 0. UI/UX: 구글 스타일 CSS 주입 ---
st.set_page_config(layout="wide", page_title="AI Legal Agent Pro", page_icon="⚖️")

st.markdown("""
<style>
    /* 전체 배경 및 폰트 */
    .stApp { background-color: #f8f9fa; font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, Roboto, sans-serif; }
    
    /* 카드 디자인 고도화 */
    .card {
        background: white;
        padding: 24px;
        border-radius: 16px;
        border: 1px solid #e0e0e0;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        margin-bottom: 24px;
        transition: transform 0.2s;
    }
    .card:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(0,0,0,0.08); }
    
    /* 타이포그래피 */
    h1 { color: #202124; font-weight: 800; letter-spacing: -0.05rem; }
    h2, h3 { color: #1a73e8; font-weight: 700; }
    .highlight { background: #e8f0fe; color: #1967d2; padding: 2px 6px; border-radius: 4px; font-weight: 600; }
    
    /* 액션 섹션 스타일 */
    .action-header { display: flex; align-items: center; gap: 10px; margin-bottom: 20px; border-bottom: 2px solid #f1f3f4; padding-bottom: 10px; }
    .preview-box { background-color: #f8f9fa; border: 1px solid #dadce0; padding: 20px; border-radius: 8px; font-family: 'Nanum Myeongjo', serif; line-height: 1.8; min-height: 400px; }
</style>
""", unsafe_allow_html=True)

# --- 1. 인프라 연결 (Gemini Pro 강제) ---
try:
    # Secrets 로드
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    SERPAPI_KEY = st.secrets["general"]["SERPAPI_KEY"]
    
    # [핵심 변경] Gemini 1.5 Pro (최신 버전) 강제 설정
    genai.configure(api_key=GEMINI_API_KEY)
    
    # 모델 생성 설정 (Temperature 0 = 창의성 죽이고 팩트 중심)
    generation_config = {
        "temperature": 0.0,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 8192,
    }
    # 안전 설정 해제 (법률 용어 필터링 방지)
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    
    model = genai.GenerativeModel(model_name="gemini-1.5-pro",
                                  generation_config=generation_config,
                                  safety_settings=safety_settings)

    try:
        SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
        SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_db = True
    except: use_db = False

except Exception as e:
    st.error(f"🚨 시스템 초기화 실패: {e}")
    st.stop()

# --- 2. 로직: 진짜 "검색"과 "필터링" ---

def search_laws_whitelist(keywords, situation):
    """[지능형 필터] 엉뚱한 법령(과거사 등)을 원천 차단"""
    candidates = []
    
    # 1. 상황별 강제 추천 (Whitelist)
    if any(x in situation for x in ["차", "주차", "견인", "방치"]):
        candidates.extend(["자동차관리법", "도로교통법", "주차장법"])
    
    # 2. API 검색
    for kw in keywords:
        try:
            url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={kw}&display=3"
            root = ET.fromstring(requests.get(url, timeout=3).content)
            for law in root.findall(".//law"):
                name = law.find("법령명한글").text
                # [Blacklist] 역사/보훈 관련 법 제외
                if not any(bad in name for bad in ["대일항쟁", "보훈", "참전", "5·18", "특수"]):
                    candidates.append(name)
        except: continue
    
    return list(set(candidates))

def get_deep_context(situation):
    """[Chain of Thought] 1.검색어추출 -> 2.법령확보 -> 3.조문매칭"""
    
    # Step 1: LLM에게 검색어 물어보기
    prompt_kw = f"상황: {situation}\n이 상황을 해결하기 위한 '현행 법령' 검색 키워드 2개만 알려줘 (예: 자동차관리법). 역사 관련 법은 절대 제외."
    kw_resp = model.generate_content(prompt_kw).text
    keywords = kw_resp.strip().split()
    
    # Step 2: 법령 API 검색 + 화이트리스트 필터
    candidates = search_laws_whitelist(keywords, situation)
    if not candidates: candidates = ["민법", "행정절차법"] # 최후의 보루
    
    # Step 3: 최적 법령 1개 선정 (Gemini Pro가 판단)
    best_law_prompt = f"상황: {situation}\n후보: {candidates}\n가장 적합한 법령 1개 이름만 출력해."
    final_law = model.generate_content(best_law_prompt).text.strip()
    
    # Step 4: 조문 가져오기 (API 호출 시뮬레이션 - 실제론 law.go.kr 상세 API 연결)
    # (속도를 위해 핵심 법령인 경우 중요 조항 하드코딩 매핑 가능, 여기선 예시)
    return final_law, f"{final_law}의 관련 조항 및 시행규칙 데이터"

# --- 3. 핵심: 보고서 및 액션 생성기 ---

def run_analysis_pipeline(situation):
    """Gemini 1.5 Pro를 갈아넣어 보고서와 액션 데이터를 생성"""
    
    law_name, law_text = get_deep_context(situation)
    
    # [프롬프트 엔지니어링] 구조화된 출력 강제
    prompt = f"""
    당신은 대한민국 최고의 행정 전문 변호사입니다.
    
    [민원 상황] {situation}
    [관련 법령] {law_name}
    
    다음 4가지 섹션을 Markdown으로 작성하세요.
    1. **핵심 요약**: 3줄 요약.
    2. **법적 검토**: {law_name}에 근거한 판단. (절대 대일항쟁기 법 등 엉뚱한 법 인용 금지)
    3. **현실적 조치**: 담당자가 해야 할 일.
    
    4. **[액션 데이터]**: 
    맨 마지막에 반드시 아래 JSON 포맷을 출력하세요. 
    이것은 사용자가 사용할 '문서 작성 도구'의 설계도입니다.
    
    ```json
    {{
        "title": "여권 재발급 반려 통지서 작성",
        "doc_type": "공문",
        "fields": [
            {{"id": "receiver", "label": "수신인", "placeholder": "홍길동"}},
            {{"id": "reason", "label": "반려 사유", "placeholder": "사진 규격 미준수 (6개월 경과)"}},
            {{"id": "date", "label": "발송일", "placeholder": "2024-00-00"}}
        ],
        "template": "문서번호: [date]-001\\n수신: [receiver]\\n\\n귀하의 민원은 [reason] 사유로 반려되었음을..."
    }}
    ```
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text, "Gemini 1.5 Pro"
    except Exception as e:
        return f"Error: {e}", "Fail"

# --- 4. 메인 UI (Google Style) ---

# 세션 상태 관리 (새로고침 방지)
if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None
if "action_json" not in st.session_state:
    st.session_state.action_json = None

st.title("🏛️ AI Legal Agent")
st.caption("Powered by Gemini 1.5 Pro | Deep Reasoning Engine")

# 입력창
with st.container():
    col1, col2 = st.columns([4, 1])
    with col1:
        user_input = st.text_area("민원 내용 입력", height=80, placeholder="예: 무단 방치 차량 강제 처리 절차가 궁금합니다.")
    with col2:
        st.write("") # Spacer
        st.write("") 
        if st.button("🚀 정밀 분석", type="primary", use_container_width=True):
            if not user_input:
                st.warning("내용을 입력하세요.")
            else:
                with st.spinner("Gemini 1.5 Pro가 법령을 대조하고 있습니다..."):
                    full_text, source = run_analysis_pipeline(user_input)
                    
                    # JSON 분리
                    json_match = re.search(r'```json\s*(\{.*?\})\s*```', full_text, re.DOTALL)
                    if json_match:
                        st.session_state.action_json = json.loads(json_match.group(1))
                        st.session_state.analysis_result = full_text.replace(json_match.group(0), "")
                    else:
                        st.session_state.analysis_result = full_text
                        st.session_state.action_json = None

# 결과 화면
if st.session_state.analysis_result:
    st.divider()
    
    # 1. 보고서 영역 (카드 스타일)
    st.markdown(f"""
    <div class="card">
        <h2>📑 법률 검토 보고서</h2>
        {st.session_state.analysis_result}
    </div>
    """, unsafe_allow_html=True)

    # 2. 액션 센터 (UI 개선: Split View)
    if st.session_state.action_json:
        data = st.session_state.action_json
        
        st.markdown(f"""
        <div class="card" style="border: 2px solid #4285f4; background-color: #f8faff;">
            <div class="action-header">
                <h3>⚡ AI Action Center: {data.get('title')}</h3>
                <span class="highlight">Auto-Drafting</span>
            </div>
        """, unsafe_allow_html=True)

        # 2단 레이아웃: 입력(Left) -> 미리보기(Right)
        col_input, col_preview = st.columns([1, 1])
        
        inputs = {}
        with col_input:
            st.subheader("📝 정보 입력")
            with st.form("doc_builder"):
                for field in data.get('fields', []):
                    inputs[field['id']] = st.text_input(
                        field['label'], 
                        placeholder=field.get('placeholder', '')
                    )
                
                # 버튼을 누르면 DB 저장 + 미리보기 갱신
                submitted = st.form_submit_button("💾 문서 생성 및 저장")

        with col_preview:
            st.subheader("📄 실시간 미리보기")
            
            # 템플릿에 입력값 적용
            final_doc = data.get('template', "")
            for k, v in inputs.items():
                if v: # 값이 있을 때만 치환
                    final_doc = final_doc.replace(f"[{k}]", v)
            
            # 종이 문서 느낌의 미리보기 창
            st.markdown(f"""
            <div class="preview-box">
                {final_doc.replace(chr(10), '<br>')}
            </div>
            """, unsafe_allow_html=True)

        # 폼 제출 후 로직
        if submitted:
            if use_db:
                try:
                    supabase.table("action_logs").insert({
                        "action_type": data['title'],
                        "inputs": inputs,
                        "final_doc": final_doc,
                        "created_at": datetime.now().isoformat()
                    }).execute()
                    st.toast("✅ DB 저장 완료! 처리 이력에 기록되었습니다.")
                except Exception as e:
                    st.error(f"저장 실패: {e}")
            else:
                st.success("문서가 생성되었습니다. (DB 연결 안됨)")
        
        st.markdown("</div>", unsafe_allow_html=True) # End Card


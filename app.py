import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
import time

# --- 1. 화면 설정 및 고해상도 디자인 ---
st.set_page_config(layout="wide", page_title="법령 분석 보고서 Pro")

st.markdown("""
    <style>
    /* 제목 및 레이아웃 스타일 */
    .section-title { font-size: 1.3rem; font-weight: bold; margin-bottom: 12px; color: #1E3A8A; border-left: 6px solid #1E3A8A; padding-left: 12px; }
    .content-box { padding: 20px; border-radius: 12px; background-color: #FFFFFF; border: 1px solid #E5E7EB; min-height: 550px; line-height: 1.8; font-size: 1.05rem; }
    
    /* 대응 절차 카드 스타일 */
    .response-step { margin-bottom: 18px; padding: 15px; background-color: #F0F9FF; border-radius: 8px; border-left: 4px solid #0EA5E9; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .step-header { color: #0369A1; font-weight: bold; font-size: 1.1rem; margin-bottom: 5px; display: block; }
    
    /* 법령 스크롤 박스 스타일 */
    .law-text { font-family: 'Malgun Gothic', sans-serif; background-color: #FFFBEB !important; border-left: 6px solid #F59E0B !important; height: 550px; overflow-y: auto; padding: 15px; }
    </style>
    """, unsafe_allow_html=True)

# API 설정 및 보안 확인
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except:
    st.error("🚨 Secrets 설정(GEMINI_API_KEY)을 확인하세요.")
    st.stop()

# --- 2. 핵심 로직: 모델 자동 감지 및 데이터 가공 ---

def get_available_model():
    """NotFound 에러 방지를 위해 사용 가능한 모델 리스트를 조회하여 자동 선택"""
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        # 1.5-flash가 있으면 우선 선택, 없으면 첫 번째 가용 모델 선택
        for m in available_models:
            if "1.5-flash" in m: return m
        return available_models[0] if available_models else None
    except:
        return None

def format_step_ui(response_data):
    """AI가 준 리스트 데이터를 깔끔한 단계별 UI로 변환"""
    if isinstance(response_data, list):
        html_output = ""
        for i, item in enumerate(response_data, 1):
            title = item.get('title', f'{i}단계')
            desc = item.get('description', '')
            html_output += f"<div class='response-step'><span class='step-header'>📍 {title}</span>{desc}</div>"
        return html_output
    return str(response_data).replace("\n", "<br>")

def fetch_law_full_text(law_name):
    """국가법령정보센터 실시간 데이터 수집"""
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
    try:
        res = requests.get(url, timeout=10)
        if "인증" in res.text or "승인" in res.text: return "NOT_APPROVED"
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None
        mst_id = law_node.find("법령일련번호").text
        real_name = law_node.find("법령명한글").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst_id}&type=XML"
        detail_res = requests.get(detail_url, timeout=15)
        detail_root = ET.fromstring(detail_res.content)
        articles = [f"제{a.find('조문번호').text}조({a.find('조문제목').text}): {a.find('조문내용').text}" 
                    for a in detail_root.findall(".//조문")[:80] if a.find('조문번호') is not None]
        return {"name": real_name, "text": "\n".join(articles)}
    except: return None

# --- 3. 실행 UI ---

st.title("⚖️ 공무원 업무 지원: 법령 기반 대응 솔루션")
query = st.text_input("상황을 입력하세요", placeholder="예: 무단 방치 차량 처리 절차 및 소유자 확인 방법")

if st.button("🚀 전문 분석 보고서 생성", type="primary"):
    if not query:
        st.warning("분석할 내용을 입력해주세요.")
    else:
        with st.status("📡 AI 에이전트 가동 및 법령 수집 중...", expanded=True) as status:
            
            # 1. 모델 자동 감지 (404 에러 원천 차단)
            model_name = get_available_model()
            if not model_name:
                st.error("❌ 현재 API 키로 사용 가능한 AI 모델이 없습니다.")
                st.stop()
            model = genai.GenerativeModel(model_name)
            
            # 2. 관련 법령 탐색
            st.write("🔍 관련 법령 식별 및 데이터 호출 중...")
            law_name_res = model.generate_content(f"'{query}' 관련 대한민국 법령명 1개만 써줘.")
            target_law = law_name_res.text.strip().replace(" ", "").replace("`", "")
            
            # 3. 데이터 수집
            law_info = fetch_law_full_text(target_law)
            if law_info == "NOT_APPROVED":
                st.error("❌ 국가법령 API가 '신청' 단계입니다. 승인이 필요합니다.")
                st.stop()
            elif not law_info:
                st.error("❌ 데이터를 가져오지 못했습니다. 법령명을 확인하세요.")
                st.stop()

            # 4. 정밀 분석 (프롬프트 강화)
            st.write("🧠 전문 조문 대조 및 대응 매뉴얼 작성 중...")
            prompt = f"""
            질문: {query}
            법령: {law_info['text']}
            전문 법률 상담사로서 JSON 형식으로만 응답하세요.
            'response'는 반드시 구체적인 단계별 리스트 [{{'title': '...', 'description': '...'}}] 형식이어야 합니다.
            {{
                "situation": "질문의 핵심 상황 요약",
                "response": [
                    {{"title": "1단계: 현장 방문 및 증거 확보", "description": "방치된 차량의 상태를 촬영하고 현장 조서를 작성합니다."}},
                    {{"title": "2단계: 자진처리 명령 고지", "description": "소유자에게 안내문을 발송하거나 차량에 부착합니다."}}
                ],
                "law_brief": "관련 법적 근거 핵심 조항 요약"
            }}
            """
            analysis_res = model.generate_content(prompt)
            
            # 5. 화면 렌더링
            json_match = re.search(r'\{.*\}', analysis_res.text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                status.update(label="🏆 분석 완료!", state="complete")
                st.divider()

                # --- 레이아웃 출력 (요청하신 2:3:5 비율) ---
                col1, col2, col3 = st.columns([2, 3, 5])
                
                with col1:
                    st.markdown("<div class='section-title'>🔍 상황 요약</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='content-box'>{result.get('situation')}</div>", unsafe_allow_html=True)
                
                with col2:
                    st.markdown("<div class='section-title'>✅ 대응 절차 (실무 가이드)</div>", unsafe_allow_html=True)
                    # "개똥" 같은 코드를 깔끔한 UI로 변환하여 출력
                    formatted_steps = format_step_ui(result.get('response'))
                    st.markdown(f"<div class='content-box' style='background-color:#F8FAFC;'>{formatted_steps}</div>", unsafe_allow_html=True)
                
                with col3:
                    st.markdown(f"<div class='section-title'>📜 법령: {law_info['name']}</div>", unsafe_allow_html=True)
                    law_detail_html = str(result.get('law_brief')).replace("\n", "<br>")
                    law_raw_html = law_info['text'][:5000].replace("\n", "<br>")
                    st.markdown(f"""
                        <div class='content-box law-text'>
                            <b>[분석 결과: 주요 근거 조문]</b><br>{law_detail_html}<hr>
                            <b>[수집 데이터: 법령 전문 요약]</b><br>{law_raw_html}...
                        </div>
                    """, unsafe_allow_html=True)

import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re

# --- 1. 화면 설정 및 커스텀 디자인 (2:4:4 비율 최적화) ---
st.set_page_config(layout="wide", page_title="법령 기반 업무 가이드", page_icon="⚖️")

st.markdown("""
    <style>
    /* 제목 스타일 */
    .section-title { font-size: 1.25rem; font-weight: bold; margin-bottom: 15px; color: #1E3A8A; border-left: 6px solid #1E3A8A; padding-left: 12px; }
    
    /* 공통 박스 스타일 */
    .report-box { padding: 20px; border-radius: 12px; background-color: #FFFFFF; border: 1px solid #E5E7EB; min-height: 550px; line-height: 1.8; font-size: 1.05rem; box-shadow: 0 2px 4px rgba(0,0,0,0.03); }
    
    /* 대응 절차(중앙) 강조 스타일 */
    .response-card { margin-bottom: 15px; padding: 15px; background-color: #F0F9FF; border-radius: 8px; border: 1px solid #BAE6FD; }
    .step-label { color: #0284C7; font-weight: bold; font-size: 1.1rem; display: block; margin-bottom: 5px; }
    
    /* 법령(우측) 스크롤 스타일 */
    .law-scroll { font-family: 'Malgun Gothic', sans-serif; background-color: #FFFBEB !important; border: 1px solid #FEF3C7 !important; height: 550px; overflow-y: auto; padding: 15px; }
    </style>
    """, unsafe_allow_html=True)

# API 설정
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except:
    st.error("🚨 Secrets 설정을 확인하세요 (550.jpg 참고).")
    st.stop()

# --- 2. 핵심 기능 함수 ---

def get_best_model():
    """사용 가능한 모델 자동 매칭 (404 에러 방지)"""
    try:
        available = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for target in ["1.5-flash", "flash", "pro"]:
            for m_name in available:
                if target in m_name: return m_name
        return available[0] if available else None
    except: return None

def fetch_law_data(law_name):
    """국가법령정보센터 실시간 데이터 수집"""
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
    try:
        res = requests.get(url, timeout=10)
        # 미승인 상태 체크
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

# --- 3. UI 메인 실행 ---

st.title("⚖️ 법령 기반 실무 가이드 시스템")
query = st.text_input("분석할 민원 또는 법적 상황을 입력하세요.")

if st.button("🚀 정밀 리포트 생성", type="primary"):
    if not query:
        st.warning("내용을 입력해주세요.")
    else:
        with st.status("📡 데이터 수집 및 전문가 분석 중...", expanded=True) as status:
            model_name = get_best_model()
            if not model_name:
                st.error("❌ AI 모델 연결 불가"); st.stop()
            model = genai.GenerativeModel(model_name)
            
            # 1. 법령 식별
            law_res = model.generate_content(f"'{query}' 관련 대한민국 법령명 1개만 써줘.")
            target_law = law_res.text.strip().replace(" ", "").replace("`", "")
            
            # 2. 법령 수집
            law_info = fetch_law_data(target_law)
            
            # API 미승인 시 대응 로직
            if law_info == "NOT_APPROVED":
                st.warning("⚠️ API 승인 대기 중입니다. AI 지식 기반 가상 리포트를 생성합니다.")
                law_info = {"name": target_law, "text": "법령 API 승인 후 실제 조문이 표시됩니다."}
            elif not law_info:
                st.error("❌ 법령 수집 실패"); st.stop()

            # 3. 상세 분석 (가독성 높은 JSON 구조 강제)
            prompt = f"""
            질문: {query}
            법령: {law_info['text']}
            전문 행정가로서 아래 JSON 형식으로만 응답하세요. 
            'response'는 반드시 단계별 리스트 [{{'title': '...', 'description': '...'}}]여야 합니다.
            {{
                "situation": "상황의 법적 성격 요약",
                "response": [
                    {{"title": "1단계: 초기 대응", "description": "구체적 행동 지침"}},
                    {{"title": "2단계: 절차 이행", "description": "법적 절차 준수 가이드"}}
                ],
                "law_brief": "주요 근거 조항 번호와 핵심 요약"
            }}
            """
            analysis_res = model.generate_content(prompt)
            
            # 4. 파싱 및 레이아웃 출력
            json_match = re.search(r'\{.*\}', analysis_res.text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                status.update(label="🏆 분석 리포트 완성!", state="complete")
                st.divider()

                # --- [2:4:4 비율 설정] ---
                col1, col2, col3 = st.columns([2, 4, 4])
                
                with col1:
                    st.markdown("<div class='section-title'>🔍 상황 요약</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='report-box'>{result.get('situation')}</div>", unsafe_allow_html=True)
                
                with col2:
                    st.markdown("<div class='section-title'>✅ 실무 대응 절차</div>", unsafe_allow_html=True)
                    steps = result.get('response', [])
                    # 개똥 같은 코드를 UI 카드로 변환
                    steps_html = "".join([f"<div class='response-card'><span class='step-label'>📍 {s['title']}</span>{s['description']}</div>" for s in steps])
                    st.markdown(f"<div class='report-box' style='background-color:#F8FAFC;'>{steps_html}</div>", unsafe_allow_html=True)
                
                with col3:
                    st.markdown(f"<div class='section-title'>📜 법령: {law_info['name']}</div>", unsafe_allow_html=True)
                    brief_html = str(result.get('law_brief')).replace("\n", "<br>")
                    full_law_html = law_info['text'].replace("\n", "<br>")
                    st.markdown(f"""
                        <div class='report-box law-scroll'>
                            <b>[핵심 근거 조문]</b><br>{brief_html}<hr>
                            <b>[법령 전문 데이터]</b><br>{full_law_html}
                        </div>
                    """, unsafe_allow_html=True)
            else:
                st.error("AI 분석 결과 파싱 실패.")

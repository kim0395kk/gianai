import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
import time

# --- 1. 화면 설정 및 디자인 ---
st.set_page_config(layout="wide", page_title="법령 분석 보고서 Pro")

st.markdown("""
    <style>
    .section-title { font-size: 1.3rem; font-weight: bold; margin-bottom: 12px; color: #1E3A8A; border-left: 6px solid #1E3A8A; padding-left: 12px; }
    .content-box { padding: 20px; border-radius: 12px; background-color: #FFFFFF; border: 1px solid #E5E7EB; min-height: 550px; line-height: 1.8; font-size: 1.05rem; }
    .response-step { margin-bottom: 18px; padding: 15px; background-color: #F0F9FF; border-radius: 8px; border-left: 4px solid #0EA5E9; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .law-text { font-family: 'Malgun Gothic', sans-serif; background-color: #FFFBEB !important; border-left: 6px solid #F59E0B !important; overflow-y: auto; }
    .step-title { color: #0369A1; font-weight: bold; font-size: 1.1rem; margin-bottom: 5px; display: block; }
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

# --- 2. 로직: 대응 절차 가공 함수 ---
def format_response_ui(response_data):
    """지저분한 리스트 데이터를 공무원 보고서용 UI로 변환"""
    if isinstance(response_data, list):
        html = ""
        for i, item in enumerate(response_data, 1):
            title = item.get('title', f'{i}단계')
            desc = item.get('description', '')
            html += f"<div class='response-step'><span class='step-title'>📍 {title}</span>{desc}</div>"
        return html
    return str(response_data).replace("\n", "<br>")

def fetch_law_data(law_name):
    """법령 API 호출"""
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

# --- 3. 메인 실행부 ---
st.title("⚖️ 법령 기반 민원 대응 솔루션 Pro")
query = st.text_input("민원 상황을 입력하세요", placeholder="예: 무단 방치 차량 신고 접수 및 처리 절차")

if st.button("🚀 정밀 분석 보고서 생성", type="primary"):
    if not query:
        st.warning("내용을 입력해주세요.")
    else:
        with st.status("📡 법령 탐색 및 대응 매뉴얼 작성 중...", expanded=True) as status:
            # 1. 모델 설정
            model = genai.GenerativeModel("gemini-1.5-flash")
            
            # 2. 법령 탐색
            st.write("🔍 관련 법령 식별 중...")
            law_name_res = model.generate_content(f"'{query}' 관련 대한민국 법령명 1개만 써줘.")
            target_law = law_name_res.text.strip().replace(" ", "").replace("`", "")
            
            # 3. 데이터 수집
            law_info = fetch_law_data(target_law)
            if law_info == "NOT_APPROVED":
                st.error("❌ API 승인이 필요합니다.")
                st.stop()
            elif not law_info:
                st.error("❌ 데이터를 가져오지 못했습니다.")
                st.stop()

            # 4. 분석 수행
            st.write("🧠 전문 조문 대조 및 매뉴얼 작성 중...")
            prompt = f"""
            질문: {query}
            법령: {law_info['text']}
            당신은 법률 전문가입니다. 아래 JSON 형식으로만 응답하세요.
            'response'는 반드시 단계별 리스트 [{{'title': '...', 'description': '...'}}] 형식이어야 합니다.
            {{
                "situation": "상황 요약",
                "response": [
                    {{"title": "1단계: 접수 및 현장 확인", "description": "민원 내용을 기록하고 현장을 방문하여 상태를 촬영합니다."}},
                    {{"title": "2단계: 법적 고지", "description": "자진거부 명령서 부착 및 소유자 파악을 실시합니다."}}
                ],
                "law_brief": "관련 조항 핵심 근거"
            }}
            """
            analysis_res = model.generate_content(prompt)
            
            # 5. 결과 파싱 및 출력
            json_match = re.search(r'\{.*\}', analysis_res.text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                status.update(label="🏆 분석 완료!", state="complete")
                
                # --- 레이아웃 출력 (2:3:5 비율) ---
                col1, col2, col3 = st.columns([2, 3, 5])
                
                with col1:
                    st.markdown("<div class='section-title'>🔍 상황 요약</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='content-box'>{result.get('situation')}</div>", unsafe_allow_html=True)
                
                with col2:
                    st.markdown("<div class='section-title'>✅ 대응 절차</div>", unsafe_allow_html=True)
                    # 데이터 가공 후 출력 (핵심!)
                    formatted_html = format_response_ui(result.get('response'))
                    st.markdown(f"<div class='content-box' style='background-color:#F8FAFC;'>{formatted_html}</div>", unsafe_allow_html=True)
                
                with col3:
                    st.markdown(f"<div class='section-title'>📜 법령: {law_info['name']}</div>", unsafe_allow_html=True)
                    law_detail_html = str(result.get('law_brief')).replace("\n", "<br>")
                    law_raw_html = law_info['text'][:4000].replace("\n", "<br>")
                    st.markdown(f"""
                        <div class='content-box law-text'>
                            <b>[핵심 근거 조문]</b><br>{law_detail_html}<hr>
                            <b>[법령 전문 요약]</b><br>{law_raw_html}...
                        </div>
                    """, unsafe_allow_html=True)
            else:
                status.update(label="분석 형식이 올바르지 않습니다.", state="error")

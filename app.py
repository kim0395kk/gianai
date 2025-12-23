import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
import time

# --- 1. 화면 설정 및 디자인 (2:4:4 비율) ---
st.set_page_config(layout="wide", page_title="법령 기반 업무 가이드", page_icon="⚖️")

st.markdown("""
    <style>
    .section-title { font-size: 1.25rem; font-weight: bold; margin-bottom: 15px; color: #1E3A8A; border-left: 6px solid #1E3A8A; padding-left: 12px; }
    .report-box { padding: 20px; border-radius: 12px; background-color: #FFFFFF; border: 1px solid #E5E7EB; min-height: 550px; line-height: 1.8; font-size: 1.05rem; box-shadow: 0 2px 4px rgba(0,0,0,0.03); }
    .response-card { margin-bottom: 15px; padding: 15px; background-color: #F0F9FF; border-radius: 8px; border: 1px solid #BAE6FD; }
    .step-label { color: #0284C7; font-weight: bold; font-size: 1.1rem; display: block; margin-bottom: 5px; }
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

# --- 2. 모델 호출 및 한도 초과 방어 함수 ---

def get_best_model():
    try:
        available = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for target in ["1.5-flash", "flash", "pro"]:
            for m_name in available:
                if target in m_name: return m_name
        return available[0] if available else None
    except: return None

def call_ai_with_quota_check(model, prompt):
    """ResourceExhausted 에러 발생 시 사용자에게 1분 대기 안내"""
    try:
        return model.generate_content(prompt)
    except Exception as e:
        if "429" in str(e) or "ResourceExhausted" in str(e):
            st.error("⚠️ **AI 사용 한도 초과!** 무료 버전은 분당 호출 횟수가 제한됩니다. **약 1분 후에** 다시 시도해주세요.")
            st.stop()
        else:
            st.error(f"❌ AI 오류 발생: {e}")
            st.stop()

def fetch_law_data(law_name):
    """국가법령정보센터 데이터 수집"""
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
    try:
        res = requests.get(url, timeout=10)
        if "인증" in res.text or "승인" in res.text: return "NOT_APPROVED" #
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

# --- 3. UI 메인 ---

st.title("⚖️ 법령 기반 실무 가이드 시스템")
query = st.text_input("분석할 상황을 입력하세요.")

if st.button("🚀 정밀 리포트 생성", type="primary"):
    if not query:
        st.warning("내용을 입력해주세요.")
    else:
        with st.status("📡 데이터 수집 및 전문가 분석 중...", expanded=True) as status:
            model_name = get_best_model()
            model = genai.GenerativeModel(model_name)
            
            # 1. 법령 식별
            law_res = call_ai_with_quota_check(model, f"'{query}' 관련 대한민국 법령명 1개만 써줘.")
            target_law = law_res.text.strip().replace(" ", "").replace("`", "")
            
            # 2. 법령 수집
            law_info = fetch_law_data(target_law)
            if law_info == "NOT_APPROVED":
                st.warning("⚠️ API 승인 대기 중입니다. 가상 리포트를 생성합니다.")
                law_info = {"name": target_law, "text": "법령 API 승인 후 실제 조문이 표시됩니다."}
            elif not law_info:
                st.error("❌ 법령 수집 실패"); st.stop()

            # 3. 상세 분석
            prompt = f"질문: {query}\n법령: {law_info['text']}\nJSON 형식으로만 답해줘: {{'situation': '요약', 'response': [{{'title': '단계', 'description': '내용'}}]}}"
            analysis_res = call_ai_with_quota_check(model, prompt)
            
            json_match = re.search(r'\{.*\}', analysis_res.text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                status.update(label="🏆 분석 완료!", state="complete")
                st.divider()

                # --- [2:4:4 비율 레이아웃] ---
                col1, col2, col3 = st.columns([2, 4, 4])
                
                with col1:
                    st.markdown("<div class='section-title'>🔍 상황 요약</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='report-box'>{result.get('situation')}</div>", unsafe_allow_html=True)
                
                with col2:
                    st.markdown("<div class='section-title'>✅ 실무 대응 절차</div>", unsafe_allow_html=True)
                    steps = result.get('response', [])
                    steps_html = "".join([f"<div class='response-card'><span class='step-label'>📍 {s['title']}</span>{s['description']}</div>" for s in steps])
                    st.markdown(f"<div class='report-box' style='background-color:#F8FAFC;'>{steps_html}</div>", unsafe_allow_html=True)
                
                with col3:
                    st.markdown(f"<div class='section-title'>📜 법령: {law_info['name']}</div>", unsafe_allow_html=True)
                    full_law_html = law_info['text'].replace("\n", "<br>")
                    st.markdown(f"<div class='report-box law-scroll'>{full_law_html}</div>", unsafe_allow_html=True)

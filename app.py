import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
import time

# --- 1. 화면 설정 및 스타일 ---
st.set_page_config(layout="wide", page_title="공무원 법령 분석 시스템", page_icon="⚖️")

st.markdown("""
    <style>
    .report-box { padding: 15px; border-radius: 8px; border: 1px solid #d1d5db; background-color: #ffffff; min-height: 250px; font-size: 1rem; }
    .law-box { height: 600px; overflow-y: auto; background-color: #fff9e6; border-left: 5px solid #f59e0b; }
    h3 { color: #111827; border-bottom: 2px solid #374151; padding-bottom: 8px; }
    </style>
    """, unsafe_allow_html=True)

# API 설정
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error("🚨 Secrets 설정을 확인하세요.")
    st.stop()

# --- 2. 모델 자동 선택 및 안전 호출 함수 ---

def get_best_available_model():
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        # 한도가 넉넉한 flash 모델을 최우선으로 찾음
        for target in ["1.5-flash", "flash", "pro"]:
            for m_name in available_models:
                if target in m_name: return m_name
        return available_models[0] if available_models else None
    except: return None

def call_gemini_with_retry(model, prompt):
    """사용량 초과 에러 발생 시 사용자에게 안내하고 멈춤"""
    try:
        return model.generate_content(prompt)
    except Exception as e:
        if "429" in str(e) or "ResourceExhausted" in str(e):
            st.error("⚠️ AI 사용 한도를 초과했습니다. 무료 버전은 분당 호출 횟수가 제한됩니다. **1분만 기다렸다가** 다시 버튼을 눌러주세요.")
            st.stop()
        else:
            st.error(f"❌ AI 오류 발생: {e}")
            st.stop()

# --- 3. 법령 데이터 수집 엔진 ---

def fetch_law_data(law_name):
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
    try:
        res = requests.get(url, timeout=10)
        # 신청 단계 체크
        if "인증" in res.text or "승인" in res.text: return "NOT_APPROVED"
        
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None
        
        mst_id = law_node.find("법령일련번호").text
        real_name = law_node.find("법령명한글").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst_id}&type=XML"
        detail_res = requests.get(detail_url, timeout=15)
        detail_root = ET.fromstring(detail_res.content)
        
        full_text = []
        for article in detail_root.findall(".//조문")[:80]:
            no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            title = article.find("조문제목").text if article.find("조문제목") is not None else ""
            content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            full_text.append(f"제{no}조({title}): {content}")
        return {"name": real_name, "text": "\n".join(full_text)}
    except: return None

# --- 4. 메인 UI 및 실행 ---

st.title("⚖️ 법령 정밀 분석 보고서")
query = st.text_input("분석할 상황을 입력하세요.")

if st.button("🚀 실시간 분석 시작", type="primary"):
    if not query:
        st.warning("분석할 내용을 입력해주세요.")
    else:
        with st.status("📡 AI 엔진 연결 및 데이터 수집 중...", expanded=True) as status:
            
            # 1. 모델 감지
            working_model_name = get_best_available_model()
            if not working_model_name:
                st.error("❌ 사용 가능한 모델이 없습니다.")
                st.stop()
            
            st.write(f"✅ 모델 연결: **{working_model_name}**")
            model = genai.GenerativeModel(working_model_name)
            
            # 2. 관련 법령명 식별 (안전 호출 적용)
            st.write("🔍 질문과 연관된 법령 찾는 중...")
            response = call_gemini_with_retry(model, f"'{query}'와 관련있는 대한민국 법령명 딱 1개만 써줘.")
            target_law = response.text.strip().replace(" ", "").replace("`", "")
            st.info(f"선정된 법령: **{target_law}**")
            
            # 3. 법령 데이터 수집
            law_data = fetch_law_data(target_law)
            if law_data == "NOT_APPROVED":
                st.error("❌ 국가법령 API가 아직 '신청' 단계입니다. 승인이 필요합니다.")
                status.update(label="API 미승인", state="error")
                st.stop()
            elif not law_data:
                st.error("❌ 데이터를 가져오지 못했습니다.")
                st.stop()

            # 4. 3개 섹션 분석 (안전 호출 적용)
            st.write("🧠 전문 조문 대조 분석 중...")
            prompt = f"질문: {query}\n법령: {law_data['text']}\n위 내용을 근거로 사실관계(situation), 대응절차(response), 상세법령근거(law_detail)를 JSON으로 작성해."
            response = call_gemini_with_retry(model, prompt)
            
            json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                status.update(label="🏆 분석 완료!", state="complete")
                
                # --- 5. 2:3:5 비율 레이아웃 ---
                col1, col2, col3 = st.columns([2, 3, 5])
                with col1:
                    st.markdown("### 🔍 상황 요약")
                    st.markdown(f"<div class='report-box'>{result.get('situation', '')}</div>", unsafe_allow_html=True)
                with col2:
                    st.markdown("### ✅ 대응 절차")
                    st.markdown(f"<div class='report-box' style='background-color: #f0fdf4;'>{result.get('response', '')}</div>", unsafe_allow_html=True)
                with col3:
                    st.markdown(f"### 📜 관련 법령: {law_data['name']}")
                    # 가공된 텍스트
                    d_html = result.get('law_detail', '').replace('\n', '<br>')
                    r_html = law_data['text'][:3000].replace('\n', '<br>')
                    st.markdown(f"""
                        <div class='report-box law-box'>
                            <b>[핵심 근거 조항]</b><br>{d_html}
                            <hr>
                            <b>[법령 원문 요약]</b><br>{r_html}...
                        </div>
                    """, unsafe_allow_html=True)

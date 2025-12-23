import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re

# --- 1. 화면 설정 (와이드 모드 및 커스텀 스타일) ---
st.set_page_config(layout="wide", page_title="공무원 법령 분석 시스템", page_icon="⚖️")

st.markdown("""
    <style>
    /* 박스 공통 스타일 */
    .report-box {
        padding: 15px;
        border-radius: 8px;
        border: 1px solid #d1d5db;
        background-color: #ffffff;
        font-size: 0.95rem;
        line-height: 1.6;
    }
    /* 법령 칸 전용 스타일 (스크롤 가능 및 높이 고정) */
    .law-box {
        height: 500px;
        overflow-y: auto;
        background-color: #fff9e6;
        border-left: 5px solid #f59e0b;
    }
    h3 { color: #111827; border-bottom: 2px solid #374151; padding-bottom: 8px; margin-bottom: 15px; }
    .stButton>button { width: 100%; }
    </style>
    """, unsafe_allow_html=True)

# API 설정
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except:
    st.error("🚨 Secrets 설정을 확인해주세요 (GEMINI_API_KEY, LAW_API_ID).")
    st.stop()

# --- 2. 핵심 로직 함수 ---

def get_working_model():
    """사용 가능한 Gemini 모델 자동 감지"""
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'gemini-1.5-flash' in m.name: return m.name
        return "models/gemini-1.5-flash"
    except: return "models/gemini-1.5-flash"

def fetch_law_data(law_name):
    """국가법령정보센터 API 데이터 수집"""
    search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
    try:
        res = requests.get(search_url, timeout=10)
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
        # 조문 내용을 더 많이 가져오도록 제한을 80개로 확대
        for article in detail_root.findall(".//조문")[:80]:
            no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            title = article.find("조문제목").text if article.find("조문제목") is not None else ""
            content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            full_text.append(f"제{no}조({title}): {content}")
            
        return {"name": real_name, "text": "\n".join(full_text)}
    except: return None

# --- 3. UI 메인 ---

st.title("⚖️ 법령 정밀 분석 보고서")
st.write("민원 상황에 따라 관련 법령을 대조하고 대응 절차를 생성합니다.")

query = st.text_input("분석할 상황 입력", placeholder="예: 민방위 교육 불참 과태료 부과 절차와 이의신청 방법")

if st.button("🚀 실시간 분석 시작"):
    if not query:
        st.warning("내용을 입력해주세요.")
    else:
        with st.status("📡 법령 데이터를 정밀 분석하고 있습니다...", expanded=True) as status:
            
            # 1. 법령명 식별
            model_name = get_working_model()
            model = genai.GenerativeModel(model_name)
            target_law_res = model.generate_content(f"'{query}'와 가장 관련있는 대한민국 법령명 1개만 써줘. 예: 민방위기본법")
            target_law = target_law_res.text.strip().replace(" ", "")
            
            st.write(f"✅ 관련 법령 식별: **{target_law}**")
            
            # 2. 법령 수집
            law_data = fetch_law_data(target_law)
            
            if law_data == "NOT_APPROVED":
                st.error("❌ 국가법령 API 승인이 필요합니다. (544.jpg 참조)")
                status.update(label="API 미승인", state="error")
                st.stop()
            elif not law_data:
                st.error("❌ 법령 데이터를 가져오지 못했습니다.")
                st.stop()

            # 3. AI 분석 (상세 법령 추출 강조)
            prompt = f"""
            질문: {query}
            법령 전문: {law_data['text']}

            위 내용을 바탕으로 아래 JSON 형식으로만 답변하세요. 
            내용이 많더라도 법령 근거를 상세히 포함하세요.
            {{
                "situation": "민원인의 상황 핵심 요약",
                "response": "공무원 입장에서의 단계별 대응 절차 및 가이드",
                "law_detail": "관련된 모든 법조항의 번호와 구체적인 내용을 상세히 기술"
            }}
            """
            response = model.generate_content(prompt)
            json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
            
            if json_match:
                result = json.loads(json_match.group())
                status.update(label="🏆 분석 완료!", state="complete")
                st.divider()

                # --- 4. 비율 조정 레이아웃 [2:3:5 비율] ---
                col1, col2, col3 = st.columns([2, 3, 5])

                with col1:
                    st.markdown("### 🔍 상황 요약")
                    st.markdown(f"<div class='report-box'>{result['situation']}</div>", unsafe_allow_html=True)

                with col2:
                    st.markdown("### ✅ 대응 절차")
                    st.markdown(f"<div class='report-box' style='background-color: #f0fdf4;'>{result['response']}</div>", unsafe_allow_html=True)

                with col3:
                    st.markdown(f"### 📜 관련 법령: {law_data['name']}")
                    # 법령 칸에 스크롤 박스 적용
                    st.markdown(f"""
                        <div class='report-box law-box'>
                            {result['law_detail'].replace('\\n', '<br>')}
                            <hr>
                            <b>[참고: 수집된 법령 원문 요약]</b><br>
                            {law_data['text'][:2000].replace('\\n', '<br>')}...
                        </div>
                    """, unsafe_allow_html=True)
            else:
                st.error("AI 분석 형식이 올바르지 않습니다. 다시 시도해주세요.")

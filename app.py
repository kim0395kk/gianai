import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re

# --- 1. 화면 설정 및 스타일 ---
st.set_page_config(layout="wide", page_title="공무원 법령 분석 시스템", page_icon="⚖️")

st.markdown("""
    <style>
    .report-box { padding: 15px; border-radius: 8px; border: 1px solid #d1d5db; background-color: #ffffff; min-height: 250px; font-size: 1rem; }
    .law-box { height: 600px; overflow-y: auto; background-color: #fff9e6; border-left: 5px solid #f59e0b; }
    h3 { color: #111827; border-bottom: 2px solid #374151; padding-bottom: 8px; }
    </style>
    """, unsafe_allow_html=True)

# Secrets 로드
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error("🚨 Secrets 설정(GEMINI_API_KEY, LAW_API_ID)을 확인하세요.")
    st.stop()

# --- 2. 핵심 로직: 사용 가능한 모델 자동 감지 ---

def get_best_available_model():
    """현재 API 키로 사용 가능한 모델 중 분석에 적합한 모델을 자동으로 찾아 반환"""
    try:
        # 지원되는 모든 모델 리스트를 가져와서 generateContent 기능이 있는 모델 탐색
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # 1순위: 1.5-flash, 2순위: 1.5-pro, 3순위: gemini-pro
        for target in ["1.5-flash", "1.5-pro", "gemini-pro"]:
            for model_name in available_models:
                if target in model_name:
                    return model_name
        return available_models[0] if available_models else None
    except:
        return None

def fetch_law_data(law_name):
    """국가법령정보센터 API 데이터 수집"""
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
    try:
        res = requests.get(url, timeout=10)
        # 신청 단계일 경우 안내
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

# --- 3. UI 및 메인 로직 ---

st.title("⚖️ 법령 정밀 분석 보고서 (자동 모델 매칭)")
query = st.text_input("분석할 상황을 입력하세요.")

if st.button("🚀 실시간 분석 시작"):
    if not query:
        st.warning("분석할 내용을 입력해주세요.")
    else:
        with st.status("📡 AI 엔진 연결 및 데이터 수집 중...", expanded=True) as status:
            
            # 1. 사용 가능한 모델 자동 감지 (연결 실패 해결책)
            st.write("🔍 시스템에 적합한 AI 모델을 탐색 중입니다...")
            working_model = get_best_available_model()
            
            if not working_model:
                st.error("❌ 현재 API 키로 사용 가능한 AI 모델을 찾을 수 없습니다. API 키 상태를 확인하세요.")
                status.update(label="AI 연결 실패", state="error")
                st.stop()
            
            st.write(f"✅ 모델 연결 성공: **{working_model}**")
            model = genai.GenerativeModel(working_model)
            
            # 2. 관련 법령명 식별
            target_law_res = model.generate_content(f"'{query}'와 가장 관련있는 대한민국 법령명 딱 1개만 써줘.")
            target_law = target_law_res.text.strip().replace(" ", "").replace("`", "")
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

            # 4. 상세 분석
            st.write("🧠 전문 조문 대조 분석 중...")
            prompt = f"질문: {query}\n법령: {law_data['text']}\n위 내용을 근거로 사실관계(situation), 대응절차(response), 상세법령근거(law_detail)를 JSON으로 작성해."
            response = model.generate_content(prompt)
            
            json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                status.update(label="🏆 분석 완료!", state="complete")
                
                # --- 5. 2:3:5 비율 레이아웃 출력 ---
                col1, col2, col3 = st.columns([2, 3, 5])
                
                with col1:
                    st.markdown("### 🔍 상황 요약")
                    st.markdown(f"<div class='report-box'>{result.get('situation', '')}</div>", unsafe_allow_html=True)
                
                with col2:
                    st.markdown("### ✅ 대응 절차")
                    st.markdown(f"<div class='report-box' style='background-color: #f0fdf4;'>{result.get('response', '')}</div>", unsafe_allow_html=True)
                
                with col3:
                    st.markdown(f"### 📜 관련 법령: {law_data['name']}")
                    # 문법 오류 방지용 텍스트 가공
                    detail_html = result.get('law_detail', '').replace('\n', '<br>')
                    raw_html = law_data['text'][:3000].replace('\n', '<br>')
                    
                    st.markdown(f"""
                        <div class='report-box law-box'>
                            <b>[핵심 근거 조항]</b><br>{detail_html}
                            <hr>
                            <b>[법령 원문 요약]</b><br>{raw_html}...
                        </div>
                    """, unsafe_allow_html=True)
            else:
                status.update(label="분석 실패", state="error")

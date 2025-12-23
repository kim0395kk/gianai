import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re

# --- 1. 화면 레이아웃 및 스타일 설정 ---
st.set_page_config(layout="wide", page_title="공무원 법령 분석 시스템", page_icon="⚖️")

st.markdown("""
    <style>
    .report-box {
        padding: 15px;
        border-radius: 8px;
        border: 1px solid #d1d5db;
        background-color: #ffffff;
        min-height: 250px;
        font-size: 1rem;
    }
    .law-box {
        height: 600px;
        overflow-y: auto;
        background-color: #fff9e6;
        border-left: 5px solid #f59e0b;
        font-family: 'Malgun Gothic', sans-serif;
    }
    h3 { color: #111827; border-bottom: 2px solid #374151; padding-bottom: 8px; }
    </style>
    """, unsafe_allow_html=True)

# API 설정 로드
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error("🚨 Secrets 설정(GEMINI_API_KEY, LAW_API_ID)을 확인하세요.")
    st.stop()

# --- 2. 핵심 로직 함수 ---

def call_ai(prompt):
    """모델명 404 에러 방지를 위한 순차 시도 로직"""
    for m_name in ["gemini-1.5-flash", "models/gemini-1.5-flash", "gemini-pro"]:
        try:
            model = genai.GenerativeModel(m_name)
            response = model.generate_content(prompt)
            return response.text
        except:
            continue
    return None

def fetch_law_data(law_name):
    """국가법령정보센터 API 데이터 수집"""
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
        
        articles = []
        for article in detail_root.findall(".//조문")[:80]:
            no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            title = article.find("조문제목").text if article.find("조문제목") is not None else ""
            content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            articles.append(f"제{no}조({title}): {content}")
        return {"name": real_name, "text": "\n".join(articles)}
    except: return None

# --- 3. UI 메인 실행 ---

st.title("⚖️ 법령 정밀 분석 보고서 (2:3:5 레이아웃)")
query = st.text_input("분석할 상황을 입력하세요.")

if st.button("🚀 실시간 분석 시작"):
    if not query:
        st.warning("질문을 입력해주세요.")
    else:
        with st.status("📡 데이터 수집 및 AI 분석 중...", expanded=True) as status:
            
            # 1. 관련 법령명 식별
            st.write("🔍 관련 법령을 식별하고 있습니다...")
            target_law_raw = call_ai(f"'{query}'와 관련된 대한민국 법령명 1개만 써줘.")
            if not target_law_raw:
                status.update(label="AI 모델 연결 실패", state="error")
                st.stop()
            target_law = target_law_raw.strip().replace(" ", "").replace("`", "")
            st.info(f"식별된 법령: **{target_law}**")
            
            # 2. 법령 전문 수집
            st.write(f"🌐 [{target_law}] 데이터를 국가 서버에서 수집 중...")
            law_data = fetch_law_data(target_law)
            
            if law_data == "NOT_APPROVED":
                st.error("❌ 국가법령 API가 아직 '신청' 단계입니다. 승인이 필요합니다.")
                status.update(label="API 미승인", state="error")
                st.stop()
            elif not law_data:
                st.error("❌ 데이터를 가져오지 못했습니다.")
                st.stop()

            # 3. 3개 섹션 분석
            st.write("🧠 전문 조문 대조 분석 중...")
            prompt = f"질문: {query}\n법령: {law_data['text']}\n위 내용을 근거로 situation, response, law_detail을 JSON으로 작성해."
            analysis_res = call_ai(prompt)
            
            json_match = re.search(r'\{.*\}', analysis_res, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                status.update(label="🏆 분석 완료!", state="complete")
                st.divider()

                # --- 4. 2:3:5 비율 레이아웃 출력 ---
                col1, col2, col3 = st.columns([2, 3, 5])
                
                with col1:
                    st.markdown("### 🔍 상황 요약")
                    st.markdown(f"<div class='report-box'>{result.get('situation', '')}</div>", unsafe_allow_html=True)
                
                with col2:
                    st.markdown("### ✅ 대응 절차")
                    st.markdown(f"<div class='report-box' style='background-color: #f0fdf4;'>{result.get('response', '')}</div>", unsafe_allow_html=True)
                
                with col3:
                    st.markdown(f"### 📜 관련 법령: {law_data['name']}")
                    # SyntaxError 방지: replace 처리를 중괄호 밖에서 미리 수행
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
                status.update(label="분석 형식이 올바르지 않습니다.", state="error")

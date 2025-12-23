import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import time
import re

# --- 1. 설정 및 API 키 확인 ---
st.set_page_config(layout="wide", page_title="공무원 AI 법률 어시스턴트")

try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error("🚨 Secrets 설정(API 키)을 확인해주세요.")
    st.stop()

# --- 2. AI 모델 호출 함수 (404 에러 방지용 보강) ---

def ask_gemini(prompt):
    """v1beta API 호환성을 위해 모델명을 가변적으로 시도"""
    # 현재 API 버전에서 가장 가능성 높은 모델 명칭 리스트
    model_candidates = ["gemini-1.5-flash", "models/gemini-1.5-flash", "gemini-pro"]
    
    for model_name in model_candidates:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            if "404" in str(e):
                continue # 다음 후보 모델로 시도
            else:
                st.error(f"AI 호출 중 오류 발생: {e}")
                return None
    
    st.error("❌ 지원되는 AI 모델을 찾을 수 없습니다. API 키 권한을 확인해주세요.")
    return None

# --- 3. 법령 데이터 관련 함수 ---

def get_target_law_name(user_query):
    prompt = f"질문: '{user_query}'\n관련 대한민국 법령명 딱 1개만 출력해. (예: 민방위기본법). 다른 말 절대 금지."
    res_text = ask_gemini(prompt)
    if res_text:
        # 응답 텍스트 정제
        clean_name = res_text.strip().replace(" ", "").replace("`", "")
        clean_name = re.sub(r'법령명:?', '', clean_name)
        return clean_name
    return None

def fetch_law_full_text(law_name):
    """국가법령정보센터 API 연동"""
    search_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {"OC": LAW_API_ID, "target": "law", "type": "XML", "query": law_name}
    try:
        res = requests.get(search_url, params=params, timeout=10)
        # 신청 단계 체크
        if "인증되지 않은" in res.text or "승인되지 않은" in res.text:
            return "NOT_APPROVED"
            
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None
        
        mst_id = law_node.find("법령일련번호").text
        real_name = law_node.find("법령명한글").text
        
        detail_url = "https://www.law.go.kr/DRF/lawService.do"
        detail_params = {"OC": LAW_API_ID, "target": "law", "MST": mst_id, "type": "XML"}
        detail_res = requests.get(detail_url, params=detail_params, timeout=15)
        detail_root = ET.fromstring(detail_res.content)
        
        full_text_list = []
        articles = detail_root.findall(".//조문")[:50] # 분석 속도를 위해 50개로 제한
        for article in articles:
            article_no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            article_content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            full_text_list.append(f"제{article_no}조: {article_content}")
            
        return {"name": real_name, "text": "\n".join(full_text_list)}
    except:
        return None

# --- 4. 메인 UI ---

st.title("⚖️ 법령 분석 서비스")
query = st.text_input("질문을 입력하세요", placeholder="예: 민방위 3년차 교육 미이수 시 과태료")

if st.button("🚀 분석 시작"):
    if not query:
        st.warning("질문을 입력해주세요.")
    else:
        with st.status("📡 데이터 분석 중...", expanded=True) as status:
            # 1단계
            st.write("🔍 **1단계: 관련 법령명 식별 중...**")
            target_law = get_target_law_name(query)
            
            if target_law:
                st.write(f"✅ 법령 식별 완료: **{target_law}**")
            else:
                status.update(label="에러: AI 모델 응답 실패", state="error")
                st.stop()

            # 2단계
            st.write("🌐 **2단계: 국가법령정보센터 데이터 호출 중...**")
            law_data = fetch_law_full_text(target_law)
            
            if law_data == "NOT_APPROVED":
                st.error("❌ API가 아직 **'신청'** 단계입니다. 승인이 필요합니다.")
                status.update(label="API 미승인", state="error")
                st.stop()
            elif not law_data:
                st.error("❌ 데이터를 가져오지 못했습니다. 법령명을 확인하세요.")
                status.update(label="수집 실패", state="error")
                st.stop()

            # 3단계
            st.write("🧠 **3단계: 조문 분석 및 답변 생성 중...**")
            prompt = f"질문: {query}\n법령: {law_data['text']}\n위 내용을 근거로 답변해줘."
            analysis = ask_gemini(prompt)
            
            if analysis:
                status.update(label="🏆 분석 완료!", state="complete")
                st.divider()
                st.markdown(analysis)
            else:
                status.update(label="분석 실패", state="error")

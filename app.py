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

# --- 2. [핵심] 사용 가능한 모델 자동 감지 함수 ---

def get_working_model():
    """현재 API 키로 사용 가능한 모델 중 가장 적합한 것을 자동 선택"""
    try:
        for m in genai.list_models():
            # generateContent를 지원하고, 이름에 'flash' 또는 'pro'가 포함된 모델 탐색
            if 'generateContent' in m.supported_generation_methods:
                if 'gemini-1.5-flash' in m.name or 'gemini-1.5-pro' in m.name:
                    return m.name
        # 위 조건에 맞는게 없으면 첫 번째 모델이라도 반환
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        return models[0] if models else None
    except Exception:
        return None

# --- 3. AI 모델 호출 함수 ---

def ask_gemini(prompt):
    model_name = get_working_model()
    if not model_name:
        st.error("❌ 현재 API 키로 사용할 수 있는 Gemini 모델이 없습니다. API 키 상태를 확인하세요.")
        return None
    
    try:
        # 감지된 모델 이름(예: models/gemini-1.5-flash)으로 호출
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        st.error(f"AI 호출 오류 ({model_name}): {e}")
        return None

# --- 4. 법령 데이터 관련 함수 ---

def get_target_law_name(user_query):
    prompt = f"질문: '{user_query}'\n관련 대한민국 법령명 1개만 출력해. (예: 민방위기본법). 다른 말 절대 금지."
    res_text = ask_gemini(prompt)
    if res_text:
        return res_text.strip().replace(" ", "").replace("`", "")
    return None

def fetch_law_full_text(law_name):
    """국가법령정보센터 API 연동"""
    search_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {"OC": LAW_API_ID, "target": "law", "type": "XML", "query": law_name}
    try:
        res = requests.get(search_url, params=params, timeout=10)
        # 신청 단계 체크 (544.jpg 참조)
        if "인증" in res.text or "승인" in res.text:
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
        articles = detail_root.findall(".//조문")[:30] # 속도를 위해 30개로 압축
        for article in articles:
            article_no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            article_content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            full_text_list.append(f"제{article_no}조: {article_content}")
            
        return {"name": real_name, "text": "\n".join(full_text_list)}
    except:
        return None

# --- 5. 메인 UI ---

st.title("⚖️ 법령 실시간 분석기 (자동 모델링)")
query = st.text_input("질문을 입력하세요.")

if st.button("🚀 분석 시작"):
    if not query:
        st.warning("질문을 입력해주세요.")
    else:
        with st.status("📡 시스템 가동 중...", expanded=True) as status:
            # 1단계
            st.write("🔍 **1단계: 사용 가능한 AI 모델 감지 및 법령 탐색...**")
            target_law = get_target_law_name(query)
            
            if target_law:
                st.write(f"✅ 법령 식별 완료: **{target_law}**")
            else:
                status.update(label="에러: 모델 연결 실패", state="error")
                st.stop()

            # 2단계
            st.write("🌐 **2단계: 국가법령정보센터 데이터 호출...**")
            law_data = fetch_law_full_text(target_law)
            
            if law_data == "NOT_APPROVED":
                st.error("❌ API가 아직 **'신청'** 단계입니다. 승인이 필요합니다.")
                status.update(label="API 미승인", state="error")
                st.stop()
            elif not law_data:
                st.error("❌ 데이터를 가져오지 못했습니다.")
                status.update(label="수집 실패", state="error")
                st.stop()

            # 3단계
            st.write("🧠 **3단계: 조문 대조 분석 중...**")
            prompt = f"질문: {query}\n법령내용: {law_data['text']}\n위 내용을 바탕으로 답변해."
            analysis = ask_gemini(prompt)
            
            if analysis:
                status.update(label="🏆 분석 완료!", state="complete")
                st.divider()
                st.markdown(analysis)

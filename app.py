import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re

# --- 1. 디자인 보강 (표 형태의 가독성) ---
st.set_page_config(layout="wide", page_title="법령 분석 보고서 Pro")

st.markdown("""
    <style>
    .section-title { font-size: 1.2rem; font-weight: bold; margin-bottom: 10px; color: #1E3A8A; border-left: 5px solid #1E3A8A; padding-left: 10px; }
    .content-box { padding: 20px; border-radius: 10px; background-color: #FFFFFF; border: 1px solid #E5E7EB; min-height: 500px; line-height: 1.8; }
    .response-step { margin-bottom: 15px; padding: 10px; background-color: #F0F9FF; border-radius: 5px; border-left: 3px solid #0EA5E9; }
    .law-text { font-family: 'Malgun Gothic', sans-serif; background-color: #FFFBEB; border-left: 5px solid #F59E0B; height: 600px; overflow-y: auto; padding: 15px; }
    </style>
    """, unsafe_allow_html=True)

# API 설정 (생략 방지용 체크)
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except:
    st.error("🚨 Secrets 설정을 확인하세요 (550.jpg 참고).")
    st.stop()

# --- 2. 로직: 대응 절차를 예쁘게 가공하는 함수 ---
def format_response(response_data):
    """지저분한 코드 형태를 깔끔한 번호 리스트로 변환"""
    if isinstance(response_data, list):
        formatted = ""
        for item in response_data:
            title = item.get('title', '단계')
            desc = item.get('description', '')
            formatted += f"<div class='response-step'><b>📍 {title}</b><br>{desc}</div>"
        return formatted
    # 단순 텍스트일 경우 줄바꿈 처리
    return str(response_data).replace("\n", "<br>")

# --- 3. 메인 실행부 ---
st.title("⚖️ 법령 기반 민원 대응 솔루션")
query = st.text_input("민원 상황을 입력하세요", placeholder="예: 무단 방치 차량 신고 접수 및 처리 절차")

if st.button("🚀 정밀 분석 보고서 생성", type="primary"):
    if not query:
        st.warning("내용을 입력해주세요.")
    else:
        with st.status("📡 법령 탐색 및 대응 매뉴얼 작성 중...", expanded=True) as status:
            # 모델 감지 및 법령 수집 (이전 로직 유지)
            model = genai.GenerativeModel("gemini-1.5-flash")
            
            # AI에게 "보고서 서식"으로 답변하도록 프롬프트 강화
            prompt = f"""
            질문: {query}
            당신은 법률 전문가입니다. 아래 형식의 JSON으로만 답변하세요.
            'response'는 반드시 단계별 리스트 형식 [{{'title': '...', 'description': '...'}}]으로 작성하세요.
            {{
                "situation": "상황 요약",
                "response": [
                    {{"title": "1단계: 접수", "description": "내용"}},
                    {{"title": "2단계: 확인", "description": "내용"}}
                ],
                "law_detail": "관련 조항 요약"
            }}
            """
            # (중략: 데이터 수집 로직)
            # ... 실제 법령 수집(fetch_law_data) 및 분석 수행 후 결과가 result에 담겼다고 가정 ...
            
            # 결과 화면 출력 (2:3:5 비율)
            col1, col2, col3 = st.columns([2, 3, 5])
            
            with col1:
                st.markdown("<div class='section-title'>🔍 상황 요약</div>", unsafe_allow_html=True)
                st.markdown(f"<div class='content-box'>{result.get('situation')}</div>", unsafe_allow_html=True)
                
            with col2:
                st.markdown("<div class='section-title'>✅ 대응 절차 (Step-by-Step)</div>", unsafe_allow_html=True)
                # 개똥 같은 코드를 사람용 언어로 변환하여 출력
                formatted_res = format_response(result.get('response'))
                st.markdown(f"<div class='content-box' style='background-color:#F8FAFC;'>{formatted_res}</div>", unsafe_allow_html=True)
                
            with col3:
                st.markdown("<div class='section-title'>📜 관련 법령 근거</div>", unsafe_allow_html=True)
                law_html = str(result.get('law_detail')).replace("\n", "<br>")
                st.markdown(f"<div class='content-box law-text'>{law_html}</div>", unsafe_allow_html=True)

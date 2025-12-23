import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import time
import re

# --- 1. 환경 설정 ---
st.set_page_config(layout="wide", page_title="공무원 AI 법률 어시스턴트")

try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except:
    st.error("🚨 Secrets 설정(API 키)을 확인해주세요.")
    st.stop()

# --- 2. 기능 함수들 ---

def get_target_law_name(user_query):
    """질문에서 법령명 추출"""
    model = genai.GenerativeModel('gemini-1.5-flash-latest')
    prompt = f"질문: '{user_query}'\n관련 대한민국 법령명 딱 1개만 출력해. (예: 민방위기본법). 다른 말 금지."
    res = model.generate_content(prompt)
    return res.text.strip().replace(" ", "").replace("`", "")

def fetch_law_full_text(law_name):
    """국가법령정보센터 데이터 수집"""
    search_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {"OC": LAW_API_ID, "target": "law", "type": "XML", "query": law_name}
    
    try:
        res = requests.get(search_url, params=params, timeout=10)
        # 승인 여부 체크 (승인 안됐으면 여기서 에러 메시지가 옴)
        if "인증" in res.text or "승인" in res.text or "제한" in res.text:
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
        articles = detail_root.findall(".//조문")[:100]
        for article in articles:
            article_no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            article_title = article.find("조문제목").text if article.find("조문제목") is not None else ""
            article_content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            full_text_list.append(f"제{article_no}조({article_title}) {article_content}")
            
        return {"name": real_name, "text": "\n".join(full_text_list)}
    except:
        return None

def analyze_with_law(user_query, law_data):
    """AI 상세 분석"""
    model = genai.GenerativeModel('gemini-1.5-flash-latest')
    law_context = law_data['text'][:15000]
    prompt = f"[법령: {law_data['name']}]\n{law_context}\n\n질문: {user_query}\n위 법령에 근거해 JSON 형식으로 답변해."
    res = model.generate_content(prompt)
    json_match = re.search(r'\{.*\}', res.text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    return None

# --- 3. UI 구성 (진행 과정 시각화) ---

st.title("⚖️ 법령 분석 에이전트")
st.caption("질문을 입력하면 실시간으로 국가법령정보를 검색하여 분석합니다.")

query = st.text_input("질문 예시: 기초수급자 자동차 소유 기준이 뭐야?")

if st.button("🚀 분석 시작", type="primary"):
    if not query:
        st.warning("질문을 입력하세요.")
    else:
        # st.status를 사용해 진행 과정을 보여줌
        with st.status("🎯 단계별 분석 진행 중...", expanded=True) as status:
            
            # 1단계: 법령 식별
            st.write("🔍 **1단계: 관련 법령 탐색 중...**")
            target_law = get_target_law_name(query)
            if target_law:
                st.write(f"✅ 관련 법령 식별 완료: **{target_law}**")
            else:
                status.update(label="법령 식별 실패", state="error")
                st.stop()
            
            time.sleep(0.5) # 눈으로 확인하기 위한 짧은 대기

            # 2단계: 데이터 수집
            st.write("🌐 **2단계: 국가법령정보센터 데이터 호출 중...**")
            law_data = fetch_law_full_text(target_law)
            
            if law_data == "NOT_APPROVED":
                st.error(f"❌ 국가법령 API가 아직 **'승인 대기'** 상태입니다.")
                st.info("법령센터 마이페이지에서 승인여부를 확인하세요. (전화: 02-2109-6446)")
                status.update(label="API 권한 없음", state="error")
                st.stop()
            elif not law_data:
                st.error("❌ 데이터를 가져오지 못했습니다.")
                status.update(label="수집 실패", state="error")
                st.stop()
            else:
                st.write(f"✅ 법령 데이터 확보 완료: **{law_data['name']}**")

            # 3단계: AI 분석
            st.write("🧠 **3단계: AI가 조문 대조 및 분석 중...**")
            result = analyze_with_law(query, law_data)
            
            if result:
                status.update(label="🏆 분석 완료!", state="complete")
                
                # 결과 표시
                st.divider()
                st.subheader("📋 분석 결과 보고서")
                col1, col2 = st.columns(2)
                with col1:
                    st.info(f"**사실관계**\n\n{result.get('facts')}")
                with col2:
                    st.success(f"**최종판단**\n\n{result.get('conclusion') or result.get('script')}")
            else:
                status.update(label="분석 실패", state="error")

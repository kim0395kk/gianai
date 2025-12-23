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

# --- 2. AI 모델 호출 함수 (에러 방지용) ---

def ask_gemini(prompt):
    """모델명 404 에러를 방지하기 위해 여러 이름을 시도함"""
    # 시도해볼 모델 명칭 후보들
    model_names = ["gemini-1.5-flash", "models/gemini-1.5-flash"]
    
    last_error = ""
    for name in model_names:
        try:
            model = genai.GenerativeModel(name)
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            last_error = str(e)
            continue # 다음 모델명으로 시도
            
    st.error(f"❌ AI 모델 호출에 실패했습니다. (마지막 오류: {last_error})")
    return None

# --- 3. 법령 데이터 관련 함수 ---

def get_target_law_name(user_query):
    prompt = f"질문: '{user_query}'\n관련 대한민국 법령명 딱 1개만 출력해. (예: 민방위기본법). 다른 말 절대 금지."
    res_text = ask_gemini(prompt)
    if res_text:
        return res_text.strip().replace(" ", "").replace("`", "").replace("법령명:", "")
    return None

def fetch_law_full_text(law_name):
    search_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {"OC": LAW_API_ID, "target": "law", "type": "XML", "query": law_name}
    try:
        res = requests.get(search_url, params=params, timeout=10)
        # API 승인 여부 체크
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
        articles = detail_root.findall(".//조문")[:80] # 토큰 절약을 위해 80개로 조정
        for article in articles:
            article_no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            article_title = article.find("조문제목").text if article.find("조문제목") is not None else ""
            article_content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            full_text_list.append(f"제{article_no}조({article_title}) {article_content}")
            
        return {"name": real_name, "text": "\n".join(full_text_list)}
    except:
        return None

# --- 4. 메인 UI 및 실행 로직 ---

st.title("⚖️ 법령 실시간 분석 에이전트")
query = st.text_input("상황을 입력하세요 (예: 기초수급자 자동차 소유 기준)")

if st.button("🚀 분석 시작", type="primary"):
    if not query:
        st.warning("질문을 입력해주세요.")
    else:
        with st.status("📡 단계별 분석 진행 중...", expanded=True) as status:
            # 1단계
            st.write("🔍 **1단계: 관련 법령 탐색 중...**")
            target_law = get_target_law_name(query)
            
            if target_law:
                st.write(f"✅ 관련 법령 식별 완료: **{target_law}**")
            else:
                status.update(label="에러: 법령명을 찾지 못했습니다.", state="error")
                st.stop()

            # 2단계
            st.write("🌐 **2단계: 국가법령정보센터 데이터 호출 중...**")
            law_data = fetch_law_full_text(target_law)
            
            if law_data == "NOT_APPROVED":
                st.error("❌ 국가법령 API가 아직 **'승인 대기'** 상태입니다.")
                st.info("법령센터 마이페이지(544.jpg 참조)에서 승인여부를 확인하세요.")
                status.update(label="API 권한 없음", state="error")
                st.stop()
            elif not law_data:
                st.error("❌ 데이터를 가져오지 못했습니다. 법령명이 정확한지 확인하세요.")
                status.update(label="수집 실패", state="error")
                st.stop()
            else:
                st.write(f"✅ 법령 데이터 확보 완료: **{law_data['name']}**")

            # 3단계
            st.write("🧠 **3단계: AI가 조문 대조 및 분석 중...**")
            prompt = f"질문: {query}\n법령내용: {law_data['text'][:15000]}\n위 내용을 바탕으로 사실관계, 법적근거, 결론을 JSON 형식으로 작성해."
            analysis_text = ask_gemini(prompt)
            
            if analysis_text:
                try:
                    # JSON 파싱
                    json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
                    result = json.loads(json_match.group())
                    status.update(label="🏆 분석 완료!", state="complete")
                    
                    st.divider()
                    c1, c2 = st.columns(2)
                    with c1: st.info(f"**📌 사실관계**\n\n{result.get('facts')}")
                    with c2: st.success(f"**✅ 최종판단**\n\n{result.get('conclusion') or result.get('script')}")
                except:
                    st.write(analysis_text) # 파싱 실패 시 텍스트라도 출력
                    status.update(label="분석 완료(비정형)", state="complete")
            else:
                status.update(label="분석 실패", state="error")

import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import time

# --- 1. 환경 설정 ---
st.set_page_config(layout="wide", page_title="Auto-Law AI Pro", page_icon="⚖️")

try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error("🚨 Secrets 설정(API 키)을 확인해주세요.")
    st.stop()

# --- 2. 법령 수집 함수 (안정성 강화) ---
def fetch_law_full_text(law_name):
    search_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {"OC": LAW_API_ID, "target": "law", "type": "XML", "query": law_name}
    try:
        res = requests.get(search_url, params=params, timeout=10)
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
        # 조문이 너무 많으면 상위 100개만 가져오도록 제한 (성능 및 토큰 절약)
        articles = detail_root.findall(".//조문")[:100]
        for article in articles:
            article_no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            article_title = article.find("조문제목").text if article.find("조문제목") is not None else ""
            article_content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            full_text_list.append(f"제{article_no}조({article_title}) {article_content}")
            
        return {"name": real_name, "text": "\n".join(full_text_list)}
    except Exception as e:
        st.error(f"법령 수집 중 물리적 오류: {e}")
        return None

# --- 3. AI 추론 함수 (JSON 파싱 보강) ---
def get_target_law_name(user_query):
    model = genai.GenerativeModel('gemini-1.5-flash') # 모델 명시적 지정
    prompt = f"질문: '{user_query}'\n이 질문을 해결하기 위한 정확한 대한민국 법령명 1개만 써줘. (예: 민방위기본법). 다른 말은 절대 금지."
    try:
        res = model.generate_content(prompt)
        return res.text.strip().replace(" ", "") # 공백 제거
    except Exception as e:
        st.error(f"법령 추론 단계 실패: {e}")
        return None

def analyze_with_law(user_query, law_data):
    model = genai.GenerativeModel('gemini-1.5-flash')
    # 법령 텍스트를 더 압축 (토큰 소모 감소)
    law_context = law_data['text'][:10000] 
    
    prompt = f"""
    당신은 법률 전문가입니다. 아래 [법령]을 근거로 [질문]을 분석하세요.
    [법령: {law_data['name']}]
    {law_context}
    [질문]: {user_query}

    반드시 아래 JSON 형식으로만 응답하세요. 다른 설명은 생략합니다.
    {{
        "facts": ["..."],
        "law_basis": [{{"article": "제O조", "content": "..."}}],
        "conclusion": "...",
        "script": "..."
    }}
    """
    try:
        res = model.generate_content(prompt)
        txt = res.text
        # JSON 문자열만 추출하는 정규식 (형식 깨짐 방지)
        json_match = re.search(r'\{.*\}', txt, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(txt.replace("```json", "").replace("```", "").strip())
    except Exception as e:
        st.error(f"상세 분석 단계 실패 (형식 오류): {e}")
        return None

import re # 상단에 선언해도 됨

# --- 4. 메인 UI ---
st.title("⚖️ Legal Matrix AI (안정화 버전)")

query = st.text_input("질문을 입력하세요", key="query_input")

if st.button("🚀 분석 시작"):
    if not query:
        st.warning("질문을 입력해주세요.")
    else:
        with st.status("단계별 분석 수행 중...", expanded=True) as status:
            # 1. 법령명 추론
            st.write("1️⃣ 법령명 찾는 중...")
            target_law = get_target_law_name(query)
            
            if not target_law:
                status.update(label="법령명 추론 실패", state="error")
                st.stop()
            
            st.info(f"결정된 법령: {target_law}")
            
            # 2. 법령 수집
            st.write("2️⃣ 법령 전문 수집 중...")
            law_data = fetch_law_full_text(target_law)
            
            if not law_data:
                st.error("국가법령정보센터에서 데이터를 가져오지 못했습니다. API 승인 상태를 확인하세요.")
                status.update(label="데이터 수집 실패", state="error")
                st.stop()
            
            # 3. AI 상세 분석
            st.write("3️⃣ 조항 대조 분석 중...")
            result = analyze_with_law(query, law_data)
            
            if result:
                status.update(label="분석 완료!", state="complete")
                st.divider()
                # 결과 렌더링 (이전과 동일)
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.subheader("📌 사실관계")
                    st.write(result['facts'])
                with c2:
                    st.subheader("⚖️ 법적근거")
                    for l in result['law_basis']:
                        st.write(f"**{l['article']}**: {l['content']}")
                with c3:
                    st.subheader("✅ 최종판단")
                    st.success(result['script'])
            else:
                status.update(label="상세 분석 실패", state="error")

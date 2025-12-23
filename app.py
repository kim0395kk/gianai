import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
import time

# --- 디자인 및 설정 (동일) ---
st.set_page_config(layout="wide", page_title="법령 기반 업무 가이드", page_icon="⚖️")

try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except:
    st.error("🚨 Secrets 설정을 확인하세요.")
    st.stop()

# --- 최적화된 핵심 함수 ---

def fetch_law_data(law_query):
    """법령 이름으로 조문 수집"""
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_query}"
    try:
        res = requests.get(url, timeout=5)
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None
        
        mst_id = law_node.find("법령일련번호").text
        real_name = law_node.find("법령명한글").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst_id}&type=XML"
        detail_res = requests.get(detail_url, timeout=5)
        detail_root = ET.fromstring(detail_res.content)
        articles = [f"제{a.find('조문번호').text}조: {a.find('조문내용').text[:200]}..." 
                    for a in detail_root.findall(".//조문")[:30]] # 토큰 절약을 위해 30개 제한
        return {"name": real_name, "text": "\n".join(articles)}
    except: return None

# --- UI 메인 ---
st.title("⚖️ 법령 기반 실무 가이드 시스템")
user_query = st.text_input("분석할 상황을 입력하세요.")

if st.button("🚀 정밀 리포트 생성", type="primary"):
    if not user_query:
        st.warning("내용을 입력해주세요.")
    else:
        with st.status("📡 최적화 분석 엔진 가동 중...", expanded=True) as status:
            model = genai.GenerativeModel("gemini-1.5-flash") # 가장 빠른 모델 사용
            
            # [전략 변경] AI에게 법령명과 가이드를 한 번에 요청 (호출 횟수 1회로 단축)
            status.write("1. 법리 검토 및 가이드라인 생성 중...")
            combined_prompt = f"""
            질문: {user_query}
            
            너는 대한민국 법률 전문가이자 베테랑 공무원이야. 
            위 질문에 대해 1) 가장 관련 깊은 법령 이름 2) 민원 대응 가이드라인을 작성해줘.
            반드시 아래 JSON 형식으로만 응답해:
            {{
              "law_name": "법령명칭(예: 대기환경보전법)",
              "situation": "상황 요약(2~3줄)",
              "response": [
                {{"title": "법적 근거", "description": "조문 근거 제시"}},
                {{"title": "민원 대응", "description": "대응 논리"}},
                {{"title": "조치 사항", "description": "안내 대안"}}
              ]
            }}
            """
            
            try:
                # 단 한 번의 호출
                response = model.generate_content(combined_prompt)
                result = json.loads(re.search(r'\{.*\}', response.text, re.DOTALL).group())
                
                # [선택 사항] 실제 법령 조문 매칭 (AI 호출 없이 API만 사용)
                status.write(f"2. {result['law_name']} 실제 조문 매칭 중...")
                actual_law = fetch_law_data(result['law_name'])
                
                status.update(label="🏆 분석 완료!", state="complete")
                
                # 결과 출력 (레이아웃 생략, 기존과 동일)
                st.divider()
                c1, c2, c3 = st.columns([3, 4, 3])
                c1.info(f"🔍 **상황 요약**\n\n{result['situation']}")
                
                guide_html = "".join([f"**{s['title']}**\n{s['description']}\n\n" for s in result['response']])
                c2.success(f"✅ **실무 가이드**\n\n{guide_html}")
                
                law_text = actual_law['text'] if actual_law else "조문을 불러올 수 없습니다."
                c3.warning(f"📜 **관련 법령: {result['law_name']}**\n\n{law_text}")

            except Exception as e:
                st.error(f"⚠️ 현재 호출량이 많습니다. 30초 후 다시 시도해 주세요. (에러: {e})")

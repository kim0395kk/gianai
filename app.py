import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re

# --- 1. 환경 설정 및 API 키 확인 ---
st.set_page_config(layout="wide", page_title="Legal Matrix AI Pro", page_icon="⚖️")

# Secrets 로드
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error("🚨 설정 오류: Streamlit Cloud의 Secrets에 [general] 섹션과 키들이 정확히 입력되었는지 확인하세요.")
    st.stop()

# --- 2. 법령 데이터 수집 엔진 ---

def fetch_law_full_text(law_name):
    """국가법령정보센터 API 연동 및 데이터 파싱"""
    # [Step 1] 법령 ID 검색
    search_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {
        "OC": LAW_API_ID,
        "target": "law",
        "type": "XML",
        "query": law_name
    }
    
    try:
        res = requests.get(search_url, params=params, timeout=10)
        if res.status_code != 200: return None
        
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if law_node is None: return None
        
        mst_id = law_node.find("법령일련번호").text
        real_name = law_node.find("법령명한글").text
        
        # [Step 2] 법령 전문(XML) 수집
        detail_url = "https://www.law.go.kr/DRF/lawService.do"
        detail_params = {
            "OC": LAW_API_ID,
            "target": "law",
            "MST": mst_id,
            "type": "XML"
        }
        detail_res = requests.get(detail_url, params=detail_params, timeout=15)
        detail_root = ET.fromstring(detail_res.content)
        
        # [Step 3] 조문 텍스트 추출
        full_text_list = []
        articles = detail_root.findall(".//조문")[:120] # 분석 가능한 범위로 제한
        for article in articles:
            article_no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            article_title = article.find("조문제목").text if article.find("조문제목") is not None else ""
            article_content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            full_text_list.append(f"제{article_no}조({article_title}) {article_content}")
            
        return {"name": real_name, "text": "\n".join(full_text_list)}
    except Exception:
        return None

# --- 3. AI 추론 엔진 (모델 경로 404 에러 수정) ---

def get_target_law_name(user_query):
    """질문에서 법령명을 추출 (404 에러 방지를 위해 모델 경로 명시)"""
    try:
        # 모델 경로를 'models/gemini-1.5-flash'로 명확히 지정
        model = genai.GenerativeModel('models/gemini-1.5-flash')
        prompt = f"질문: '{user_query}'\n이 질문과 가장 밀접한 대한민국 법령명 딱 1개만 출력해. (예: 민방위기본법). 부연설명 절대 금지."
        res = model.generate_content(prompt)
        return res.text.strip().replace(" ", "").replace("`", "")
    except Exception as e:
        st.error(f"법령명 추출 중 오류: {e}")
        return None

def analyze_with_law(user_query, law_data):
    """법령 전문 기반 3단 분석 수행"""
    try:
        model = genai.GenerativeModel('models/gemini-1.5-flash')
        # 토큰 한도 초과 방지를 위한 텍스트 슬라이싱
        law_context = law_data['text'][:20000]
        
        prompt = f"""
        당신은 대한민국 법률 전문가입니다. 아래 [법령]을 근거로 [민원 질문]을 분석하세요.
        [법령: {law_data['name']}]
        {law_context}
        [민원인 질문]: {user_query}

        반드시 아래 JSON 형식으로만 응답하세요.
        {{
            "facts": ["질문에서 파악된 핵심 사실 1", "사실 2"],
            "law_basis": [
                {{"article": "제O조", "content": "해당 조항의 핵심 요지"}},
                {{"article": "제X조", "content": "관련된 조항 내용"}}
            ],
            "conclusion": "판단 결과 요약",
            "script": "민원인에게 답변할 부드러운 말투의 멘트"
        }}
        """
        res = model.generate_content(prompt)
        txt = res.text
        # JSON 문자열만 정규식으로 안전하게 추출
        json_match = re.search(r'\{.*\}', txt, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        st.error(f"상세 분석 중 오류: {e}")
        return None

# --- 4. 메인 UI 화면 ---

st.markdown("<h1 style='text-align:center;'>🏛️ Legal Matrix AI Pro</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center; color:#94A3B8;'>국가법령정보센터 실시간 연동 분석 엔진</p>", unsafe_allow_html=True)
st.divider()

# 사용자 입력창
query = st.text_input("분석할 민원이나 상황을 입력하세요.", placeholder="예: 민방위 3년차인데 교육 안 받으면 어떻게 돼?")

if st.button("🚀 실시간 법령 분석 시작", type="primary"):
    if not query:
        st.warning("먼저 내용을 입력해 주세요.")
    else:
        with st.status("AI 법률 에이전트 가동 중...", expanded=True) as status:
            # 1. 법령명 식별
            st.write("🔍 관련 법령을 파악하고 있습니다...")
            target_law = get_target_law_name(query)
            
            if not target_law:
                status.update(label="법령 식별 실패", state="error")
                st.stop()
            
            st.info(f"선정된 법령: **{target_law}**")
            
            # 2. 법령 수집 (API 연동)
            st.write(f"🌐 국가법령정보센터에서 '{target_law}' 데이터를 수집 중입니다...")
            law_data = fetch_law_full_text(target_law)
            
            if not law_data:
                # API 승인 대기 또는 ID 오류 발생 시
                st.error(f"'{target_law}'의 데이터를 가져오는 데 실패했습니다.")
                st.markdown("""
                **원인 가능성:**
                1. 국가법령정보센터의 **API 승인**이 아직 '신청' 상태인 경우 (승인까지 시간이 소요됩니다).
                2. Secrets의 **LAW_API_ID**가 틀렸거나 승인되지 않은 경우.
                """)
                status.update(label="데이터 수집 실패", state="error")
                st.stop()
            
            st.success(f"법령 수집 완료: {law_data['name']}")
            
            # 3. AI 상세 분석
            st.write("🧠 법령 조항 대조 및 판단 생성 중...")
            result = analyze_with_law(query, law_data)
            
            if result:
                status.update(label="분석이 완료되었습니다!", state="complete")
                st.divider()
                
                # 결과 3단 레이아웃 출력
                col1, col2, col3 = st.columns([1, 1.2, 1.3], gap="large")
                
                with col1:
                    st.markdown("### 📌 사실관계")
                    for f in result.get('facts', []): st.write(f"- {f}")
                
                with col2:
                    st.markdown("### ⚖️ 법적근거")
                    for l in result.get('law_basis', []):
                        st.markdown(f"**{l['article']}**\n\n{l['content']}\n---")
                
                with col3:
                    st.markdown("### ✅ 최종판단")
                    st.error(f"결론: {result.get('conclusion')}")
                    st.success(f"**답변 가이드:**\n\n{result.get('script')}")
            else:
                status.update(label="상세 분석 실패", state="error")

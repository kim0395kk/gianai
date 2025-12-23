import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import time

# --- 1. 기본 설정 ---
st.set_page_config(layout="wide", page_title="Auto-Law AI Pro", page_icon="⚖️")

# 비밀키 로드 및 에러 처리
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error("🚨 Secrets 설정 오류! Streamlit Settings > Secrets에 키를 입력했는지 확인하세요.")
    st.stop()

# --- 2. 법령 검색 및 본문 추출 로직 ---

def fetch_law_full_text(law_name):
    """국가법령정보센터 API 호출 및 XML 파싱"""
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
        
        detail_res = requests.get(detail_url, params=detail_params, timeout=10)
        detail_root = ET.fromstring(detail_res.content)
        
        full_text_list = []
        for article in detail_root.findall(".//조문"):
            article_no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            article_title = article.find("조문제목").text if article.find("조문제목") is not None else ""
            article_content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            
            paragraphs = [p.find("항내용").text for p in article.findall(".//항") if p.find("항내용") is not None]
            combined = f"제{article_no}조({article_title}) {article_content} " + " ".join(paragraphs)
            full_text_list.append(combined)
            
        return {"name": real_name, "text": "\n".join(full_text_list)}
    except:
        return None

# --- 3. AI 추론 로직 (에러 핸들링 포함) ---

def call_gemini_safely(prompt):
    """API 할당량 초과 에러를 잡기 위한 안전 호출 함수"""
    model = genai.GenerativeModel('gemini-2.0-flash')
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        if "429" in str(e) or "ResourceExhausted" in str(e):
            st.error("⚠️ AI 사용량이 너무 많습니다. 30초만 기다렸다가 다시 시도해 주세요.")
        else:
            st.error(f"❌ AI 분석 중 오류 발생: {str(e)}")
        return None

def get_target_law_name(user_query):
    prompt = f"질문: '{user_query}'\n이 질문을 해결하기 위한 가장 정확한 대한민국 법령명 1개만 딱 써줘. 예: 건축법. 다른 말은 절대 하지 마."
    res_text = call_gemini_safely(prompt)
    return res_text.strip() if res_text else None

def analyze_with_law(user_query, law_data):
    law_context = law_data['text'][:30000] # 토큰 제한 고려
    prompt = f"""
    당신은 법률 전문가입니다. 아래 [법령 전문]을 근거로 [민원 질문]을 분석하세요.
    [법령: {law_data['name']}]
    {law_context}
    
    [민원 질문]
    {user_query}
    
    반드시 아래 JSON 포맷으로만 응답하세요.
    {{
        "facts": ["사실1", "사실2"],
        "law_basis": [{{"article": "제O조", "content": "내용 요약"}}],
        "conclusion": "판단 결과",
        "script": "민원인 답변 멘트"
    }}
    """
    res_text = call_gemini_safely(prompt)
    if not res_text: return None
    try:
        # JSON 문자열 정제 (마크다운 제거)
        clean_json = res_text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except:
        return None

# --- 4. UI 구성 ---

st.markdown("<h1 style='text-align: center;'>⚖️ Legal Matrix AI</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: gray;'>법령 자동 수집 및 실시간 분석 시스템</p>", unsafe_allow_html=True)
st.divider()

# 입력 섹션
query = st.text_input("질문을 입력하세요", placeholder="예: 민방위 3년차 교육 안 받으면 벌금 얼마?")

if st.button("🚀 분석 시작", type="primary"):
    if not query:
        st.warning("질문을 입력해 주세요.")
    else:
        with st.status("🔍 법령 분석 중...", expanded=True) as status:
            # 1단계: 법령명 찾기
            st.write("1. 관련 법령 추론 중...")
            target_law = get_target_law_name(query)
            
            if target_law:
                st.info(f"검색 대상: **{target_law}**")
                
                # 2단계: API 호출
                st.write("2. 국가법령정보센터 데이터 수집 중...")
                law_data = fetch_law_full_text(target_law)
                
                if law_data:
                    st.success(f"법령 확보 완료: {law_data['name']}")
                    
                    # 3단계: AI 분석
                    st.write("3. 법령 대조 및 답변 생성 중...")
                    result = analyze_with_law(query, law_data)
                    
                    if result:
                        status.update(label="분석 완료!", state="complete")
                        
                        # 결과 UI 출력
                        st.divider()
                        c1, c2, c3 = st.columns([1, 1.2, 1.2])
                        with c1:
                            st.subheader("📌 사실 관계")
                            for f in result['facts']: st.info(f)
                        with c2:
                            st.subheader("⚖️ 법적 근거")
                            for l in result['law_basis']:
                                st.markdown(f"**{l['article']}**\n\n{l['content']}\n---")
                        with c3:
                            st.subheader("✅ 판단 및 조치")
                            st.error(f"결론: {result['conclusion']}")
                            st.write("**답변 가이드:**")
                            st.success(result['script'])
                    else:
                        status.update(label="분석 실패", state="error")
                else:
                    status.update(label="법령 수집 실패", state="error")
                    st.error("국가법령정보센터에서 법령을 찾지 못했습니다. 법령명을 정확히 입력하거나 API 설정을 확인하세요.")
            else:
                status.update(label="법령 추론 실패", state="error")

import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re

# --- 1. 기본 설정 및 비밀키 로드 ---
st.set_page_config(layout="wide", page_title="Auto-Law AI", page_icon="⚖️")

try:
    # 스트림릿 시크릿에서 키 가져오기
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"] # 국가법령센터 OC값
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error(f"비밀키 설정이 필요합니다. (.streamlit/secrets.toml): {e}")
    st.stop()

# --- 2. [핵심] 국가법령정보센터 API 연동 (XML 파싱) ---

def fetch_law_full_text(law_name):
    """
    1. 법령명으로 검색해서 ID(MST)를 찾고
    2. 그 ID로 본문 전문(XML)을 가져와서 텍스트만 추출함
    """
    # [Step 1] 법령 검색 (ID 찾기)
    search_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {
        "OC": LAW_API_ID,
        "target": "law",
        "type": "XML",
        "query": law_name
    }
    
    try:
        res = requests.get(search_url, params=params)
        root = ET.fromstring(res.content)
        
        # 검색 결과 개수 확인
        total_cnt = root.find("totalCnt")
        if total_cnt is None or int(total_cnt.text) == 0:
            return None # 검색 결과 없음
            
        # 정확도를 위해 첫 번째 결과의 ID(MST) 사용
        law_node = root.find(".//law")
        mst_id = law_node.find("법령일련번호").text
        real_name = law_node.find("법령명한글").text # 실제 검색된 법령명
        
        # [Step 2] 법령 본문 상세 조회
        detail_url = "https://www.law.go.kr/DRF/lawService.do"
        detail_params = {
            "OC": LAW_API_ID,
            "target": "law",
            "MST": mst_id,
            "type": "XML"
        }
        
        detail_res = requests.get(detail_url, params=detail_params)
        detail_root = ET.fromstring(detail_res.content)
        
        # [Step 3] XML에서 조문 내용만 싹 긁어오기
        # (조문번호, 조문내용, 항내용 등을 합쳐서 텍스트로 만듦)
        full_text_list = []
        for article in detail_root.findall(".//조문"):
            article_no = article.find("조문번호").text if article.find("조문번호") is not None else ""
            article_title = article.find("조문제목").text if article.find("조문제목") is not None else ""
            article_content = article.find("조문내용").text if article.find("조문내용") is not None else ""
            
            # 항 내용도 포함
            paragraphs = []
            for p in article.findall(".//항"):
                p_content = p.find("항내용").text
                if p_content: paragraphs.append(p_content.strip())
                
            combined = f"제{article_no}조({article_title}) {article_content} " + " ".join(paragraphs)
            full_text_list.append(combined)
            
        return {"name": real_name, "text": "\n".join(full_text_list)}

    except Exception as e:
        return None

# --- 3. AI 두뇌 (Gemini) ---

def get_target_law_name(user_query):
    """사용자 질문을 듣고 검색할 '법령명' 1개를 추론"""
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"""
    사용자의 질문을 해결하기 위해 대한민국 국가법령정보센터에서 검색해야 할
    가장 정확한 '법령명' 단 1개만 출력해. (띄어쓰기 없이 정확한 명칭)
    
    질문: {user_query}
    
    예시:
    "민방위 안가면 벌금?" -> 민방위기본법
    "요양병원 건축 가능해?" -> 건축법
    "기초수급자 탈락했어" -> 국민기초생활보장법
    
    출력:
    """
    res = model.generate_content(prompt)
    return res.text.strip()

def analyze_with_law(user_query, law_data):
    """법령 전문을 참고하여 답변 생성"""
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    # 법령이 너무 길 경우를 대비해 앞부분 30,000자만 사용 (Gemini Flash는 더 많이 가능하지만 안전하게)
    law_context = law_data['text'][:50000] 
    
    prompt = f"""
    당신은 대한민국 최고의 행정 법률 전문가입니다.
    아래 [법령 전문]을 철저히 분석하여 [민원인 질문]에 답하세요.
    
    [참고 법령: {law_data['name']}]
    {law_context}
    
    [민원인 질문]
    {user_query}
    
    [필수 지침]
    1. 반드시 위 법령에 있는 조항만을 근거로 삼을 것.
    2. 답변은 JSON 형식으로만 출력할 것.
    
    [출력 JSON 포맷]
    {{
        "facts": ["질문에서 파악된 핵심 사실 1", "핵심 사실 2"],
        "law_basis": [
            {{"article": "제OO조(제목)", "content": "해당 조항의 핵심 내용 요약"}},
            {{"article": "제OO조의2", "content": "관련된 또 다른 조항"}}
        ],
        "conclusion": "결론 (가능/불가능/과태료 부과 등 명확하게)",
        "script": "민원인에게 안내할 부드럽고 전문적인 답변 멘트 (법적 근거 포함)"
    }}
    """
    res = model.generate_content(prompt)
    try:
        return json.loads(res.text.replace("```json", "").replace("```", ""))
    except:
        return None

# --- 4. UI 구성 (Streamlit) ---

st.title("🏛️ Auto-Law : 실시간 법령 분석기")
st.caption("질문하면 AI가 **국가법령정보센터**를 뒤져서 법적 근거를 찾아옵니다.")

with st.sidebar:
    st.header("사용 가이드")
    st.info("1. 상황을 구체적으로 입력하세요.\n2. AI가 법령을 검색합니다.\n3. 법적 근거와 답변을 생성합니다.")
    st.divider()
    st.text(f"연동 API ID:\n{LAW_API_ID[:4]}****")

# 메인 입력
query = st.text_area("민원 내용 또는 궁금한 점을 입력하세요.", height=100, 
                     placeholder="예: 민방위 3년차인데 사이버교육 안 받으면 과태료 얼마야? 법적 근거 알려줘.")

if st.button("🚀 AI 법률 분석 시작", type="primary"):
    if not query:
        st.warning("질문을 입력해주세요.")
    else:
        # 1. 법령명 추론
        with st.status("🔍 AI가 분석을 시작합니다...", expanded=True) as status:
            st.write("1. 관련 법령 추론 중...")
            target_law_name = get_target_law_name(query)
            st.info(f"검색 대상: **[{target_law_name}]**")
            
            # 2. 실제 API 호출
            st.write("2. 국가법령정보센터 서버 접속 중...")
            law_data = fetch_law_full_text(target_law_name)
            
            if not law_data:
                status.update(label="법령 검색 실패", state="error")
                st.error(f"국가법령정보센터에서 '{target_law_name}'을 찾을 수 없습니다. (API 키 확인 필요)")
            else:
                st.success(f"'{law_data['name']}' 전문 다운로드 완료! (글자수: {len(law_data['text'])}자)")
                
                # 3. 분석 및 생성
                st.write("3. 조항 대조 및 분석 보고서 생성 중...")
                result = analyze_with_law(query, law_data)
                
                if result:
                    status.update(label="분석 완료!", state="complete")
                    
                    # --- 결과 화면 출력 (3단 구성) ---
                    st.divider()
                    st.subheader(f"⚖️ 법률 검토 보고서 ({law_data['name']})")
                    
                    c1, c2, c3 = st.columns([1, 1.2, 1.2], gap="large")
                    
                    # [좌측] 사실관계
                    with c1:
                        st.markdown("#### 1. 사실 관계")
                        for fact in result.get("facts", []):
                            st.info(f"📌 {fact}")

                    # [중앙] 법적 근거
                    with c2:
                        st.markdown("#### 2. 법적 근거")
                        for item in result.get("law_basis", []):
                            st.markdown(f"""
                            <div style="background:#f8f9fa; padding:15px; border-radius:8px; border-left:4px solid #4dabf7; margin-bottom:10px;">
                                <div style="font-weight:bold; color:#1c7ed6;">📜 {item['article']}</div>
                                <div style="font-size:0.9em; color:#495057; margin-top:5px;">{item['content']}</div>
                            </div>
                            """, unsafe_allow_html=True)

                    # [우측] 결론 및 스크립트
                    with c3:
                        st.markdown("#### 3. 검토 의견")
                        st.success(f"결론: {result.get('conclusion')}")
                        
                        st.markdown(f"""
                        <div style="background:#e6fcf5; padding:20px; border-radius:8px; border:1px solid #20c997;">
                            <strong>🗣️ 답변 가이드:</strong><br><br>
                            {result.get('script')}
                        </div>
                        """, unsafe_allow_html=True)
                        
                    # (선택) 원문 보기
                    with st.expander("참고한 법령 원문(일부) 보기"):
                        st.text(law_data['text'][:1000] + "\n...(후략)...")


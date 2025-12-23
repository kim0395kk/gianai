import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
from supabase import create_client, Client
from tenacity import retry, stop_after_attempt, wait_exponential

# --- 설정 ---
st.set_page_config(layout="wide", page_title="공무원 업무 내비게이션", page_icon="⚖️")

try:
    # Secrets에서 키 가져오기
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
    
    genai.configure(api_key=GEMINI_API_KEY)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"🚨 설정 오류: {e}")
    st.stop()

# --- 모델 호출 (gemini-1.5-flash 고정) ---
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def call_ai(prompt):
    # 404 에러 방지를 위해 확실한 모델명 사용
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content(prompt)
    return response.text

# --- 법령 검색 ---
def fetch_law(query):
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={query}"
    try:
        res = requests.get(url, timeout=10)
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        if not law_node: return None
        
        mst = law_node.find("법령일련번호").text
        name = law_node.find("법령명한글").text
        
        det_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        det_res = requests.get(det_url, timeout=10)
        det_root = ET.fromstring(det_res.content)
        
        articles = [f"제{a.find('조문번호').text}조: {a.find('조문내용').text}" 
                    for a in det_root.findall(".//조문")[:50]]
        return {"name": name, "text": "\n".join(articles)}
    except: return None

# --- UI ---
st.title("⚖️ 공무원 업무 내비게이션 (DB연동)")

situation = st.text_area("상황을 입력하세요 (예: 노상 적치물 강제 수거 절차)")

if st.button("🚀 가이드 생성", type="primary"):
    if not situation:
        st.warning("내용을 입력해주세요.")
    else:
        with st.status("분석 중...", expanded=True) as status:
            # 1. 법령 찾기
            status.write("관련 법령 찾는 중...")
            law_name = call_ai(f"질문: {situation}\n관련 법령 이름 1개만(예: 도로법)").strip().replace("*","")
            
            # 2. 내용 가져오기
            law_data = fetch_law(law_name)
            if not law_data: st.error("법령을 못 찾았습니다."); st.stop()
            
            # 3. 분석하기
            status.write("실무 가이드 작성 중...")
            prompt = f"""
            상황: {situation}
            법령: {law_data['text']}
            공무원 실무 가이드를 JSON으로 작성해:
            {{
                "summary": "3줄 요약",
                "steps": [{{"step": "1단계", "desc": "내용"}}, {{"step": "2단계", "desc": "내용"}}],
                "tip": "팁"
            }}
            """
            res_text = call_ai(prompt)
            json_match = re.search(r'\{.*\}', res_text, re.DOTALL)
            report = json.loads(json_match.group())
            
            # 4. 저장하기 (테이블이 있으니 성공할 것임)
            status.write("DB에 저장 중...")
            supabase.table("law_reports").insert({
                "situation": situation,
                "law_name": law_data['name'],
                "summary": report['summary'],
                "steps": json.dumps(report['steps'], ensure_ascii=False),
                "tip": report['tip']
            }).execute()
            
            status.update(label="완료!", state="complete")
            
            st.success("✅ 분석 결과가 저장되었습니다!")
            st.write(f"**요약:** {report['summary']}")
            for s in report['steps']:
                st.info(f"**{s['step']}**: {s['desc']}")
            st.warning(f"💡 팁: {report['tip']}")

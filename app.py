import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
from serpapi import GoogleSearch
import re
import time
from supabase import create_client
from groq import Groq 

# --- 0. 디자인 및 초기 설정 ---
st.set_page_config(layout="wide", page_title="AI 행정관: The Legal Glass (vFinal)", page_icon="⚖️")

st.markdown("""
<style>
    .stApp { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); }
    div[data-testid="stVerticalBlock"] > div[style*="background-color"] {
        background: rgba(255, 255, 255, 0.85);
        box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1);
        backdrop-filter: blur(8px);
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.4);
        padding: 25px;
        margin-bottom: 20px;
    }
    h1, h2, h3 { color: #1a237e !important; font-family: 'Helvetica Neue', sans-serif; }
    strong { color: #1a237e; background-color: rgba(26, 35, 126, 0.1); padding: 2px 4px; border-radius: 4px; }
    .status-badge { background-color: #dbeafe; color: #1e40af; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; }
    .groq-badge { background-color: #fce7f3; color: #9d174d; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; border: 1px solid #fbcfe8; }
</style>
""", unsafe_allow_html=True)

# --- 1. API 연결 ---
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    SERPAPI_KEY = st.secrets["general"]["SERPAPI_KEY"]
    GROQ_API_KEY = st.secrets["general"].get("GROQ_API_KEY", None)

    try:
        SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
        SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_db = True
    except: 
        use_db = False

    genai.configure(api_key=GEMINI_API_KEY)
    
    if GROQ_API_KEY:
        groq_client = Groq(api_key=GROQ_API_KEY)
    else:
        groq_client = None

except Exception as e:
    st.error(f"🚨 시스템 설정 오류: {e}")
    st.stop()

# 모델 우선순위 설정 (최신 -> 안정 -> 고성능)
GEMINI_PRIORITY_LIST = [
    "gemini-2.0-flash-exp", 
    "gemini-1.5-flash", 
    "gemini-1.5-pro"
]
GROQ_MODEL = "llama-3.3-70b-versatile"

# --- 2. 스마트 하이브리드 엔진 (Gemini + Groq Expert) ---
def generate_content_hybrid(prompt, temp=0.1):
    """
    1. Gemini 모델들을 순서대로 시도
    2. 전부 실패 시 Groq(Llama 3.3)에 '전문가 페르소나'를 입혀 실행
    """
    # 1. Gemini 시도
    for model_name in GEMINI_PRIORITY_LIST:
        try:
            model = genai.GenerativeModel(model_name)
            # 타임아웃을 8초로 짧게 주어 빠른 전환 유도
            res = model.generate_content(prompt, request_options={'timeout': 8})
            return res.text, f"Gemini ({model_name})"
        except Exception:
            continue # 다음 모델 시도

    # 2. Groq 시도 (최후의 보루)
    if groq_client:
        try:
            # [전문가 모드 시스템 프롬프트]
            system_role = """
            당신은 대한민국 최고의 행정법 전문 변호사입니다.
            1. 판례와 법령에 기반하여 냉철하고 전문적인 어조로 답변하십시오.
            2. 추측성 발언을 삼가고, 주어진 데이터(Context) 내에서만 근거를 찾으십시오.
            3. 답변은 논리적 구조(마크다운)를 갖춰야 합니다.
            """
            
            chat_completion = groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_role},
                    {"role": "user", "content": prompt}
                ],
                model=GROQ_MODEL,
                temperature=temp,
                max_completion_tokens=4000
            )
            return chat_completion.choices[0].message.content, "Groq (Llama 3.3 Expert)"
        except Exception as groq_e:
            return f"모든 AI 응답 실패 (Error: {groq_e})", "Fail"
    else:
        return "Gemini 연결 실패 및 Groq 키 없음.", "Fail"

# --- 3. [ReAct] 법령 판단 강화 엔진 ---

def search_candidates_from_api(keywords):
    """[Action] 키워드로 API를 실제 검색하여 실존 법령명 후보 확보"""
    candidates = set()
    for kw in keywords:
        try:
            url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={kw}&display=3"
            res = requests.get(url, timeout=3)
            root = ET.fromstring(res.content)
            for law in root.findall(".//law"):
                candidates.add(law.find("법령명한글").text)
        except: continue
    return list(candidates)

def get_law_context_advanced(situation, callback):
    """[Reasoning -> Action -> Selection] 3단계 정밀 법령 탐색"""
    callback(10, "🤔 법률 쟁점 분석 및 키워드 추출 중...")
    
    # 1. Reasoning: 키워드 추출
    prompt_kw = f"상황: {situation}\n이 상황과 관련된 대한민국 법령 검색용 핵심 키워드 3개만 쉼표로 구분해 출력해. (예: 주차, 아파트, 도로교통)"
    keywords_str, _ = generate_content_hybrid(prompt_kw)
    keywords = [k.strip() for k in keywords_str.split(',') if k.strip()]
    if not keywords: keywords = ["행정", "민원"]
    
    callback(30, f"🔎 국가법령정보센터 실시간 조회 중... ({', '.join(keywords)})")
    
    # 2. Action: 실존 법령 후보군 검색
    candidates = search_candidates_from_api(keywords)
    if not candidates:
        return "법령 검색 실패", "관련된 정확한 법령을 찾을 수 없습니다."

    callback(50, f"⚖️ 최적 법령 선별 중... (후보: {len(candidates)}개)")
    
    # 3. Selection: AI가 후보 중 최적 법령 선택
    prompt_sel = f"""
    [민원 상황] {situation}
    [검색된 실존 법령 후보] {', '.join(candidates)}
    
    위 후보 중 민원인 상황에 가장 적합한 법령 1개의 '정확한 이름'만 출력해. 설명 금지.
    """
    best_law_name, _ = generate_content_hybrid(prompt_sel)
    best_law_name = best_law_name.strip().replace("'", "").replace('"', "")
    
    # 후보군 매칭 (AI 환각 방지)
    final_name = next((cand for cand in candidates if cand in best_law_name), candidates[0])
    
    callback(70, f"📜 '{final_name}' 상세 조문 추출 중...")
    
    # 4. Retrieval: 상세 조문 가져오기 (100개 + 항 내용 포함)
    try:
        # 정확한 법령명으로 MST ID 조회
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={final_name}"
        root = ET.fromstring(requests.get(search_url, timeout=5).content)
        mst = root.find(".//MST").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_root = ET.fromstring(requests.get(detail_url, timeout=8).content)
        
        articles = []
        for a in detail_root.findall(".//조문")[:100]: # 조문 100개 제한
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            sub_clauses = []
            for sub in a.findall(".//항"):
                s_num = sub.find('항번호').text or ""
                s_cont = sub.find('항내용').text or ""
                sub_clauses.append(f"  ({s_num}) {s_cont}")
            articles.append(f"[제{num}조] {cont}\n" + "\n".join(sub_clauses))
            
        return final_name, "\n".join(articles)
    except Exception as e:
        return final_name, f"상세 데이터 로드 실패: {e}"

# --- 4. 검색 및 보고서 작성 ---

def get_search_results(situation, callback):
    """유사 판례 검색"""
    callback(80, "🔍 유사 행정 심판 및 판례 검색 중...")
    try:
        params = {"engine": "google", "q": f"{situation} 행정처분 판례", "api_key": SERPAPI_KEY, "num": 3}
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        return "\n".join([f"- {item['title']}: {item['snippet']}" for item in results])
    except: return "(검색 결과 없음)"

def generate_final_report(situation, law_name, law_text, search_text, callback):
    """최종 보고서 작성 (환각 방지 프롬프트 적용)"""
    
    prompt = f"""
    당신은 20년 경력의 행정 전문관입니다. 
    반드시 아래 제공된 [관련 법령 데이터]의 내용을 근거로 답변해야 하며, 없는 내용을 지어내면(Hallucination) 안 됩니다.
    
    [민원 내용] {situation}
    
    [적용 법령: {law_name}]
    {law_text}
    
    [참고 판례]
    {search_text}
    
    ---
    위 정보를 바탕으로 전문적인 마크다운 보고서를 작성하세요.
    
    ## 💡 핵심 요약
    (3줄 요약)
    
    ## 📜 법적 검토
    (가장 중요: 위 법령 데이터의 '제O조 제O항'을 구체적으로 인용하여 적법/위법 여부를 논리적으로 서술)
    
    ## 👣 조치 계획
    (민원인이 밟아야 할 행정 절차 및 대응 방안)
    
    ## 📄 답변 초안
    (민원인에게 보낼 정중하고 명확한 답변 메시지)
    """
    
    callback(90, "🧠 심층 분석 및 보고서 작성 중...")
    res, source = generate_content_hybrid(prompt)
    callback(100, "완료!")
    return res, source

# --- 5. UI 실행 ---

st.markdown(f"""
<div style="text-align:center; padding: 20px;">
    <h1 style="color:#1a237e;">⚖️ AI 행정관: The Legal Glass</h1>
    <div style="margin-top: 10px;">
        <span class="status-badge">Main: Gemini 2.0/1.5</span>
        <span class="groq-badge">Backup: Groq (Expert Mode)</span>
    </div>
</div>
""", unsafe_allow_html=True)

with st.container():
    st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)
    user_input = st.text_area("민원 상황을 구체적으로 입력해주세요", height=120, placeholder="예: 아파트 단지 내 개인형 이동장치(킥보드) 불법 주차 수거 가능 여부")
    btn = st.button("🚀 정밀 법리 분석 시작", type="primary", use_container_width=True)

if btn and user_input:
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update_status(p, t):
        progress_bar.progress(p)
        status_text.caption(f"{t}")
        time.sleep(0.1)

    # 1. 정밀 법령 탐색 (ReAct 로직)
    law_name, law_text = get_law_context_advanced(user_input, update_status)
    
    # 2. 판례 검색
    search_text = get_search_results(user_input, update_status)
    
    # 3. 보고서 작성
    final_text, used_source = generate_final_report(user_input, law_name, law_text, search_text, update_status)
    
    progress_bar.empty()
    status_text.empty()
    
    st.divider()
    
    # 결과 알림 배너
    if "Groq" in used_source:
        st.warning(f"⚡ 구글 서버 과부하로 **{used_source}**가 분석했습니다.", icon="⚡")
    elif used_source == "Fail":
        st.error(f"분석 실패: {final_text}")
    else:
        st.success(f"✨ **{used_source}**가 분석을 완료했습니다. (적용법령: {law_name})", icon="🤖")

    # 마크다운 출력
    sections = re.split(r'(?=## )', final_text)
    for section in sections:
        if not section.strip(): continue
        with st.container():
            st.markdown(section)

    # DB 저장 (옵션)
    if use_db and used_source != "Fail":
        try:
            supabase.table("law_reports").insert({
                "situation": user_input,
                "law_name": law_name,
                "summary": final_text[:500],
                "ai_model": used_source
            }).execute()
        except: pass

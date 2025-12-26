import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
from serpapi import GoogleSearch
import re
import time
import json
from supabase import create_client
from groq import Groq 

# --- 0. 디자인 및 초기 설정 ---
st.set_page_config(layout="wide", page_title="AI 행정관: The Legal Glass (Ultimate)", page_icon="⚖️")

st.markdown("""
<style>
    .stApp { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); }
    div[data-testid="stVerticalBlock"] > div[style*="background-color"] {
        background: rgba(255, 255, 255, 0.9);
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

# --- 1. API 연결 및 예외처리 ---
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

# 모델 우선순위 (최신 -> 안정 -> 고성능)
GEMINI_PRIORITY_LIST = [
    "gemini-2.0-flash-exp", 
    "gemini-1.5-flash", 
    "gemini-1.5-pro"
]
GROQ_MODEL = "llama-3.3-70b-versatile"

# --- 2. 하이브리드 엔진 (Smart Fallback) ---
def generate_content_hybrid(prompt, temp=0.1):
    """
    1. Gemini 모델 순차 시도
    2. 실패 시 Groq(Llama 3.3) 실행 (전문가 페르소나 주입)
    """
    # 1. Gemini 시도
    for model_name in GEMINI_PRIORITY_LIST:
        try:
            model = genai.GenerativeModel(model_name)
            res = model.generate_content(prompt, request_options={'timeout': 8})
            return res.text, f"Gemini ({model_name})"
        except Exception:
            continue

    # 2. Groq 시도
    if groq_client:
        try:
            # [전문가 모드 시스템 프롬프트]
            system_role = """
            당신은 대한민국 최고의 행정법 전문 변호사입니다.
            1. 판례와 법령에 기반하여 냉철하고 전문적인 어조로 답변하십시오.
            2. 추상적인 답변 대신 실질적인 해결책을 제시하십시오.
            3. 답변은 마크다운 형식을 준수하십시오.
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
            return f"AI 응답 실패 (Error: {groq_e})", "Fail"
    else:
        return "Gemini 연결 실패 및 Groq 키 없음.", "Fail"

# --- 3. [Advanced Logic] 스마트 법령 필터링 ---

def get_relevant_articles(detail_root, situation):
    """
    [Core Tech] 법령 전체를 다 가져오는 게 아니라,
    사용자 상황(Situation)과 연관된 '법률 용어'가 포함된 조문만 필터링.
    """
    # 1. 사용자 입력을 '법률 매핑 키워드'로 변환
    mapping_keywords = ["금지", "관리", "처분", "과태료", "벌칙", "의무", "안전", "제1조"]
    
    # 동적 매핑 추가
    if "킥보드" in situation or "자전거" in situation or "이동장치" in situation:
        mapping_keywords.extend(["통행", "장애", "적치", "이동", "도로"])
    if "주차" in situation:
        mapping_keywords.extend(["주차", "교통", "방해", "견인"])
    if "소음" in situation:
        mapping_keywords.extend(["소음", "진동", "환경", "차음"])
    if "아파트" in situation or "단지" in situation:
        mapping_keywords.extend(["입주자", "관리주체", "공용", "전유"])
        
    filtered_articles = []
    
    # XML 파싱 및 필터링
    for a in detail_root.findall(".//조문"):
        num = a.find('조문번호').text or ""
        cont = a.find('조문내용').text or ""
        
        # 항/호 내용까지 텍스트로 합쳐서 검색
        full_text = cont
        sub_clauses = []
        for sub in a.findall(".//항"):
            s_num = sub.find('항번호').text or ""
            s_cont = sub.find('항내용').text or ""
            full_text += f" {s_cont}"
            sub_clauses.append(f"  ({s_num}) {s_cont}")
            
        # [Filter Logic] 매핑된 키워드가 하나라도 있으면 가져옴
        if any(kw in full_text for kw in mapping_keywords):
            article_str = f"[제{num}조] {cont}\n" + "\n".join(sub_clauses)
            filtered_articles.append(article_str)
            
    # 필터링 결과가 너무 적으면(3개 미만), 기본 조항(앞쪽 30개) 가져옴 (Fallback)
    if len(filtered_articles) < 3:
        for a in detail_root.findall(".//조문")[:30]:
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            filtered_articles.append(f"[제{num}조] {cont}")
        
    return filtered_articles

def search_candidates_from_api(keywords):
    """[Action] 키워드로 API를 실제 검색하여 실존 법령명 후보 확보"""
    candidates = set()
    for kw in keywords:
        if not kw or len(kw) < 2: continue
        try:
            url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={kw}&display=3"
            res = requests.get(url, timeout=3)
            root = ET.fromstring(res.content)
            for law in root.findall(".//law"):
                candidates.add(law.find("법령명한글").text)
        except: continue
    return list(candidates)

def get_law_context_advanced(situation, callback):
    """[Reasoning -> Action -> Selection -> Filtering]"""
    callback(10, "🤔 법률 쟁점 분석 및 키워드 추출 중...")
    
    # 1. [Reasoning] JSON 포맷 강제
    prompt_kw = f"""
    상황: {situation}
    관련 법령 검색을 위한 키워드 3개를 JSON으로 추출해.
    {{ "keywords": ["단어1", "단어2", "단어3"] }}
    """
    keywords_json, model_src = generate_content_hybrid(prompt_kw)
    
    try:
        json_match = re.search(r'\{.*\}', keywords_json, re.DOTALL)
        if json_match:
            keywords = json.loads(json_match.group()).get("keywords", [])
        else:
            keywords = re.findall(r'[가-힣]+', keywords_json)
            keywords = [k for k in keywords if len(k) > 1][:3]
    except:
        keywords = ["행정", "민원"]

    callback(30, f"🔎 ({model_src}) 검색어: {', '.join(keywords)}")
    
    # 2. [Action] 법령 검색
    candidates = search_candidates_from_api(keywords)
    
    # 검색 실패 시 광역 검색
    if not candidates:
        callback(40, "⚠️ 정밀 검색 실패. 광역 검색 시도...")
        broad_keywords = ["공동주택", "도로교통", "경범죄", "집합건물"]
        candidates = search_candidates_from_api(broad_keywords)
    
    if not candidates:
        candidates = ["공동주택관리법", "도로교통법"] # Default

    callback(50, f"⚖️ 최적 법령 선별 중... (후보: {len(candidates)}개)")
    
    # 3. [Selection] 최적 법령 선택
    prompt_sel = f"상황: {situation}\n후보: {', '.join(candidates)}\n가장 적합한 법령 1개 이름만 출력."
    best_law_name, _ = generate_content_hybrid(prompt_sel)
    best_law_name = re.sub(r"[\"'\[\]]", "", best_law_name).strip()
    
    final_name = next((cand for cand in candidates if cand in best_law_name), candidates[0])
    
    callback(70, f"📜 '{final_name}' 데이터 정밀 분석 및 필터링 중...")
    
    # 4. [Retrieval + Smart Filtering]
    try:
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={final_name}"
        root = ET.fromstring(requests.get(search_url, timeout=5).content)
        mst = root.find(".//MST").text
        
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_res = requests.get(detail_url, timeout=10)
        detail_root = ET.fromstring(detail_res.content)
        
        # [Google Engineer's Touch] 스마트 필터링 적용
        articles = get_relevant_articles(detail_root, situation)
        
        return final_name, "\n".join(articles)
        
    except Exception as e:
        # [Fallback] API가 터져도 AI 지식으로 답변하게 유도 (빈 리턴 방지)
        return final_name, f"(시스템 데이터 로드 오류: {e}). 하지만 당신의 법률 지식을 총동원하여 답변하세요."

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
    """최종 보고서 작성 (AI 지식 활용 허용)"""
    
    prompt = f"""
    당신은 대한민국 최고의 행정법 전문 변호사입니다.
    
    [민원 내용] {situation}
    [적용 법령: {law_name}]
    
    [법령 데이터 Context]
    {law_text[:15000]} 
    
    [지시사항]
    1. 위 [법령 데이터 Context]에 관련 조항이 있다면 반드시 인용하세요.
    2. **중요:** 만약 Context에 딱 맞는 조항이 없거나 데이터가 부족하다면, "데이터 없음"이라고 답하지 말고 **당신이 알고 있는 '{law_name}'의 일반적인 법리와 판례 지식을 총동원하여** 가장 실질적인 답변을 작성하세요.
    3. 민원인에게 도움이 되는 구체적인 해결책(신고처, 내용증명, 관리규약 확인 등)을 제시하세요.
    
    ## 💡 핵심 요약
    ## 📜 법적 검토 (조항 인용 또는 법리 해석)
    ## 👣 조치 계획 (현실적 대안)
    ## 📄 답변 초안
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
        <span class="status-badge">Main: Gemini (2.0/1.5)</span>
        <span class="groq-badge">Backup: Groq (Llama 3.3 Expert)</span>
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

    # 1. 정밀 법령 탐색 (Advanced Logic)
    law_name, law_text = get_law_context_advanced(user_input, update_status)
    
    # 2. 판례 검색
    search_text = get_search_results(user_input, update_status)
    
    # 3. 보고서 작성
    final_text, used_source = generate_final_report(user_input, law_name, law_text, search_text, update_status)
    
    progress_bar.empty()
    status_text.empty()
    
    st.divider()
    
    # 결과 알림
    if "Groq" in used_source:
        st.warning(f"⚡ 구글 서버 과부하로 **{used_source}**가 분석했습니다.", icon="⚡")
    elif used_source == "Fail":
        st.error(f"분석 실패: {final_text}")
    else:
        st.success(f"✨ **{used_source}**가 분석을 완료했습니다. (적용법령: {law_name})", icon="🤖")

    # 결과 출력
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

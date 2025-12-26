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
st.set_page_config(layout="wide", page_title="AI 행정관: The Legal Glass (Tenbagger Ed.)", page_icon="⚖️")

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
            # 빠른 전환을 위해 타임아웃 8초 설정
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
            2. 추측성 발언을 삼가고, 주어진 데이터 내에서 근거를 찾으십시오.
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

# --- 3. [Tenbagger Logic] 법령 판단 및 검색 강화 ---

def search_candidates_from_api(keywords):
    """[Action] 키워드로 API를 실제 검색하여 실존 법령명 후보 확보"""
    candidates = set()
    for kw in keywords:
        if not kw or len(kw) < 2: continue # 너무 짧은 키워드 무시
        try:
            url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={kw}&display=3"
            res = requests.get(url, timeout=3)
            root = ET.fromstring(res.content)
            for law in root.findall(".//law"):
                candidates.add(law.find("법령명한글").text)
        except: continue
    return list(candidates)

def get_law_context_advanced(situation, callback):
    """
    [Reasoning -> Action -> Selection] + [Fail-Safe Strategy]
    Llama가 멍청하게 굴어도 코드로 보정하여 반드시 법령을 찾아내는 로직
    """
    callback(10, "🤔 법률 쟁점 분석 및 키워드 추출 중...")
    
    # 1. [Reasoning] JSON 포맷 강제
    prompt_kw = f"""
    상황: {situation}
    
    위 상황과 관련된 대한민국 법령 검색용 '핵심 키워드' 3~4개를 추출해.
    1. 구체적 키워드 (예: 전동킥보드, 층간소음)
    2. 포괄적 키워드 (예: 공동주택관리법, 도로교통법, 경범죄처벌법)
    
    반드시 아래 JSON 형식으로만 출력해. 설명 금지.
    {{
        "keywords": ["단어1", "단어2", "단어3"]
    }}
    """
    
    keywords_json, model_src = generate_content_hybrid(prompt_kw)
    
    # [Parsing] JSON 파싱 및 정제
    try:
        # JSON 부분만 추출 (Backtick 제거)
        json_match = re.search(r'\{.*\}', keywords_json, re.DOTALL)
        if json_match:
            json_str = json_match.group()
            keywords = json.loads(json_str).get("keywords", [])
        else:
            raise ValueError("No JSON found")
    except:
        # 파싱 실패 시 정규식으로 한글 단어만 강제 추출 (비상 조치)
        keywords = re.findall(r'[가-힣]+', keywords_json)
        keywords = [k for k in keywords if len(k) > 1 and k != "키워드"]

    # 키워드 비었을 때 기본값
    if not keywords: keywords = ["민법", "행정"]
    
    callback(30, f"🔎 ({model_src}) 검색어: {', '.join(keywords)}")
    
    # 2. [Action] 계층적 검색 전략 (Layered Search)
    candidates = search_candidates_from_api(keywords)
    
    # 전략 B: 1차 검색 실패 시 '상황 텍스트' 일부로 광역 검색
    if not candidates:
        callback(40, "⚠️ 정밀 검색 실패. 광역 검색 시도...")
        broad_keywords = ["공동주택", "집합건물", "도로교통", "경범죄", "민법"]
        # 상황 텍스트 앞 10글자에서 명사형 추정 단어 추출
        sim_kw = situation[:15].replace(" ", "")
        candidates = search_candidates_from_api([sim_kw]) + search_candidates_from_api(broad_keywords)
        
    # 전략 C: 최후의 보루 (절대 빈 리스트를 리턴하지 않음)
    if not candidates:
        candidates = ["민법", "행정절차법", "공동주택관리법"]

    callback(50, f"⚖️ 최적 법령 선별 중... (후보: {len(candidates)}개)")
    
    # 3. [Selection] AI가 후보 중 최적 법령 선택
    prompt_sel = f"""
    [민원 상황] {situation}
    [검색된 실존 법령 후보] {', '.join(candidates)}
    
    위 후보 중 상황에 가장 적합한 법령 1개의 '정확한 이름'만 출력해.
    """
    best_law_name, _ = generate_content_hybrid(prompt_sel)
    best_law_name = re.sub(r"[\"'\[\]]", "", best_law_name).strip()
    
    # 후보군 매칭 (AI 환각 방지)
    final_name = next((cand for cand in candidates if cand in best_law_name), candidates[0])
    
    callback(70, f"📜 '{final_name}' 상세 조문 추출 중...")
    
    # 4. [Retrieval] 상세 조문 가져오기
    try:
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={final_name}"
        root = ET.fromstring(requests.get(search_url, timeout=5).content)
        
        # 정확도 보정을 위해 첫 번째 결과의 ID 사용
        try:
            mst = root.find(".//MST").text
        except:
             return final_name, "법령 상세 정보를 가져오는데 실패했습니다. (API 연동 오류)"

        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_root = ET.fromstring(requests.get(detail_url, timeout=8).content)
        
        articles = []
        for a in detail_root.findall(".//조문")[:100]: # 조문 100개
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
        return final_name, f"시스템 오류로 조문을 가져오지 못했습니다: {e}"

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
    """최종 보고서 작성"""
    
    prompt = f"""
    당신은 20년 경력의 행정 전문관입니다. 
    반드시 아래 제공된 [관련 법령 데이터]를 근거로 답변해야 하며, 없는 내용을 지어내면 안 됩니다.
    
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

    # 1. 정밀 법령 탐색 (Tenbagger Logic)
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

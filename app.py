import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
from serpapi import GoogleSearch
import re
import time
from supabase import create_client
from groq import Groq 

# --- 0. 디자인 시스템 & 설정 ---
st.set_page_config(layout="wide", page_title="AI 행정관: The Legal Glass", page_icon="⚖️")

st.markdown("""
<style>
    .stApp { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); }
    div[data-testid="stVerticalBlock"] > div[style*="background-color"] {
        background: rgba(255, 255, 255, 0.75);
        box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1);
        backdrop-filter: blur(8px);
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.4);
        padding: 25px;
        margin-bottom: 20px;
    }
    h1, h2, h3 { color: #1a237e !important; font-family: 'Helvetica Neue', sans-serif; }
    strong { color: #1a237e; background-color: rgba(26, 35, 126, 0.05); padding: 2px 4px; border-radius: 4px; }
    .status-badge { background-color: #dbeafe; color: #1e40af; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; }
    .groq-badge { background-color: #fce7f3; color: #9d174d; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; border: 1px solid #fbcfe8; }
</style>
""", unsafe_allow_html=True)

# --- 1. API 및 클라이언트 초기화 ---
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
    st.error(f"🚨 API 키 설정 오류: {e}")
    st.stop()

# 모델 설정
GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODEL_NAME = "gemini-1.5-flash" 

# --- 2. 하이브리드 엔진 ---
def generate_content_hybrid(prompt, temp=0.3): # 법률 분석이므로 창의성(temp)을 낮춤
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        res = model.generate_content(prompt, request_options={'timeout': 10})
        return res.text, "Gemini"
    except Exception as e:
        if groq_client:
            try:
                chat_completion = groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=GROQ_MODEL,
                    temperature=temp,
                )
                return chat_completion.choices[0].message.content, "Groq"
            except Exception as groq_e:
                return f"Groq 전환 실패: {groq_e}", "Error"
        else:
            return f"Gemini 오류 및 Groq 키 없음. {e}", "Error"

# --- 3. 비즈니스 로직 (강화됨) ---

def get_law_context(situation, callback):
    """[1단계] 법령 식별 및 대량 조문 조회"""
    callback(10, "📜 관련 법령 식별 중...")
    
    prompt = f"상황: {situation}\n가장 관련성 높은 대한민국 법령명 1개만 정확히 출력해 (예: 도로교통법). 부가 설명 절대 금지."
    law_name_raw, source = generate_content_hybrid(prompt)
    
    if source == "Error": return "식별 실패", ""
    law_name = re.sub(r'[^가-힣]', '', law_name_raw)
    
    callback(30, f"🏛️ '{law_name}' 전체 조문 조회 중... ({source} 엔진)")

    try:
        # 1. 법령 검색
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
        root = ET.fromstring(requests.get(search_url, timeout=5).content)
        
        try:
            mst = root.find(".//법령일련번호").text
            real_name = root.find(".//법령명한글").text
        except:
            return law_name, ""

        # 2. 상세 조문 조회
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_root = ET.fromstring(requests.get(detail_url, timeout=8).content)
        
        # [핵심 수정] 조문 개수를 10개 -> 100개로 대폭 증가
        articles = []
        for a in detail_root.findall(".//조문")[:100]: 
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            # 항 내용까지 꼼꼼히 가져오기
            sub_clauses = []
            for sub in a.findall(".//항"):
                sub_num = sub.find('항번호').text or ""
                sub_cont = sub.find('항내용').text or ""
                sub_clauses.append(f"  ({sub_num}) {sub_cont}")
            
            full_article = f"[제{num}조] {cont}\n" + "\n".join(sub_clauses)
            articles.append(full_article)
            
        callback(50, f"✅ 법령 데이터 확보 (조문 {len(articles)}개)")
        return real_name, "\n".join(articles)
    except Exception as e:
        return law_name, ""

def get_search_results(situation, callback):
    """[2단계] 유사 사례 검색"""
    callback(60, "🔍 유사 행정 사례 검색 중...")
    try:
        params = {"engine": "google", "q": f"{situation} 행정처분 판례 사례", "api_key": SERPAPI_KEY, "num": 3}
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        snippets = [f"- {item['title']}: {item['snippet']}" for item in results]
        return "\n".join(snippets)
    except:
        return "(검색 결과 없음)"

def generate_final_report(situation, law_name, law_text, search_text, callback):
    """[3단계] 심층 법률 검토 보고서 작성"""
    
    # [핵심 수정] 입력 텍스트 제한 해제 (law_text[:3000] 삭제)
    # [핵심 수정] 프롬프트에 구체적인 조항 인용 지시 추가
    prompt = f"""
    당신은 대한민국 행정법 전문 변호사이자 행정관입니다.
    제공된 법령 데이터를 꼼꼼히 분석하여 민원인의 상황에 정확히 적용되는 '법적 검토 보고서'를 작성하세요.
    
    [민원 내용]
    {situation}
    
    [검색된 유사 사례]
    {search_text}

    [관련 법령 데이터 (전체)]
    {law_name}
    {law_text} 
    
    ---
    
    ## 💡 핵심 요약
    (3줄 이내로 핵심 결론을 요약)
    
    ## 📜 상세 법적 검토
    **반드시 위 [관련 법령 데이터]에 있는 구체적인 조항(제O조 제O항)을 직접 인용**하여 분석하세요.
    - 해당 법령이 민원인의 상황에 적용되는 근거
    - 위법성 또는 적법성 판단 (법 조항에 근거하여)
    - 예외 조항이 있다면 해당 여부
    
    ## 🔍 유사 사례 및 판례 분석
    (검색된 사례를 바탕으로 실제 행정/법원 판단 경향 분석)
    
    ## 👣 구체적 조치 계획
    (민원인이 취해야 할 단계별 행동 요령)
    
    ## 📄 답변 초안 (정중하고 전문적으로)
    (민원인에게 발송할 최종 답변 텍스트)
    """
    
    callback(80, "🧠 심층 법리 해석 및 보고서 작성 중...")
    
    res_text, source = generate_content_hybrid(prompt)
    
    if source == "Error":
        time.sleep(1)
        res_text, source = generate_content_hybrid(prompt)
        if source == "Error":
            return f"분석 실패: {res_text}", "Fail"

    callback(100, "🎉 분석 완료!")
    return res_text, source

# --- 4. UI 실행 ---

st.markdown(f"""
<div style="text-align:center; padding: 20px; background: rgba(255,255,255,0.6); border-radius: 20px; border: 1px solid rgba(255,255,255,0.4);">
    <h1 style="color:#1a237e;">⚖️ AI 행정관: The Legal Glass (Pro)</h1>
    <div style="margin-top: 10px;">
        <span class="status-badge">Main: {GEMINI_MODEL_NAME}</span>
        <span class="groq-badge">Backup: Llama-3.3 (Groq)</span>
    </div>
</div>
<br>
""", unsafe_allow_html=True)

with st.container():
    st.markdown('<div style="background-color:rgba(0,0,0,0);"></div>', unsafe_allow_html=True)
    user_input = st.text_area("민원 상황 입력", height=100, placeholder="예: 무단 증축된 건물에 대한 이행강제금 부과 처분이 부당하다고 생각됩니다.")
    btn = st.button("🚀 심층 분석 시작", use_container_width=True, type="primary")

if btn and user_input:
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update_status(p, t):
        progress_bar.progress(p)
        status_text.caption(f"{t}")
        time.sleep(0.1)

    law_name, law_text = get_law_context(user_input, update_status)
    search_text = get_search_results(user_input, update_status)
    final_text, used_source = generate_final_report(user_input, law_name, law_text, search_text, update_status)
    
    progress_bar.empty()
    status_text.empty()
    
    st.divider()
    
    if used_source == "Groq":
        st.warning("⚡ Gemini 용량 초과로 **Llama 3.3 (Groq)**이 대신 정밀 분석했습니다.", icon="⚡")
    elif used_source == "Fail":
        st.error(f"분석 실패: {final_text}")
    else:
        st.success(f"✨ **Gemini**가 정밀 분석을 완료했습니다.", icon="⚖️")

    sections = re.split(r'(?=## )', final_text)
    for section in sections:
        if not section.strip(): continue
        with st.container():
            st.markdown(section)

    if use_db and used_source != "Fail":
        try:
            supabase.table("law_reports").insert({
                "situation": user_input,
                "law_name": law_name,
                "summary": final_text[:500],
                "ai_model": used_source
            }).execute()
        except: pass

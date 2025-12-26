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
    # Secrets 로드
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    SERPAPI_KEY = st.secrets["general"]["SERPAPI_KEY"]
    GROQ_API_KEY = st.secrets["general"].get("GROQ_API_KEY", None)

    # Supabase (선택 사항)
    try:
        SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
        SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_db = True
    except: 
        use_db = False

    # Gemini 설정
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Groq 설정
    if GROQ_API_KEY:
        groq_client = Groq(api_key=GROQ_API_KEY)
    else:
        groq_client = None

except Exception as e:
    st.error(f"🚨 API 키 설정 오류: {e}")
    st.stop()

# 모델 상수 정의
GROQ_MODEL = "llama-3.3-70b-versatile"
# [수정] 2.5 버전 등 불안정한 모델을 피하고 1.5 Flash로 고정
GEMINI_MODEL_NAME = "gemini-1.5-flash" 

# --- 2. 핵심 엔진: 하이브리드 생성기 (강력한 예외처리) ---
def generate_content_hybrid(prompt, temp=0.7):
    """
    [핵심] Gemini 시도 -> 실패 시(어떤 에러든) -> Groq 전환
    Returns: (text, source_name)
    """
    # 1. Gemini 시도
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        # Gemini는 timeout을 짧게 주어 빨리 실패하게 함 (5초)
        res = model.generate_content(prompt, request_options={'timeout': 8})
        return res.text, "Gemini"
        
    except Exception as e:
        # [중요] 429 에러 뿐만 아니라 모든 에러(Exception) 발생 시 Groq로 전환
        error_msg = str(e)
        print(f"Gemini Error: {error_msg}") # 로그 확인용

        # 2. Groq 시도
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
            return f"Gemini 오류(Quota 등) 발생 & Groq 키 없음. 에러: {error_msg}", "Error"

# --- 3. 비즈니스 로직 ---

def get_law_context(situation, callback):
    """[1단계] 상황에 맞는 법령명 식별"""
    callback(10, "📜 관련 법령 식별 중...")
    
    prompt = f"상황: {situation}\n가장 관련성 높은 대한민국 법령명 1개만 정확히 출력해 (예: 도로교통법). 부가 설명 절대 금지."
    law_name_raw, source = generate_content_hybrid(prompt)
    
    if source == "Error": return "식별 실패", ""
    
    law_name = re.sub(r'[^가-힣]', '', law_name_raw) # 한글만 남김
    
    callback(30, f"🏛️ '{law_name}' 조회 중... ({source} 엔진)")

    # 법령 API 조회 (국가법령정보센터)
    try:
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
        root = ET.fromstring(requests.get(search_url, timeout=5).content)
        
        # 검색 결과 파싱
        try:
            mst = root.find(".//법령일련번호").text
            real_name = root.find(".//법령명한글").text
        except:
            # 검색 안되면 그냥 원본 이름 리턴
            return law_name, ""

        # 상세 조문 조회
        detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
        detail_root = ET.fromstring(requests.get(detail_url, timeout=5).content)
        
        articles = []
        for a in detail_root.findall(".//조문")[:10]: # 최대 10개 조문만
            num = a.find('조문번호').text or ""
            cont = a.find('조문내용').text or ""
            articles.append(f"[제{num}조] {cont}")
            
        callback(50, f"✅ 법령 데이터 확보 완료")
        return real_name, "\n".join(articles)
    except Exception as e:
        return law_name, ""

def get_search_results(situation, callback):
    """[2단계] 유사 사례 검색"""
    callback(60, "🔍 유사 행정 사례 검색 중...")
    try:
        params = {"engine": "google", "q": f"{situation} 행정처분 사례 판례", "api_key": SERPAPI_KEY, "num": 3}
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        snippets = [f"- {item['title']}: {item['snippet']}" for item in results]
        return "\n".join(snippets)
    except:
        return "(검색 결과 없음)"

def generate_final_report(situation, law_name, law_text, search_text, callback):
    """[3단계] 최종 보고서 작성"""
    
    # 프롬프트 구성
    prompt = f"""
    당신은 대한민국 최고의 행정 전문관입니다.
    아래 정보를 바탕으로 민원인에게 제공할 전문적인 보고서를 마크다운 형식으로 작성하세요.
    
    [민원 내용] {situation}
    [관련 법령] {law_name}\n{law_text[:3000]} 
    [참고 사례] {search_text}
    
    ## 💡 핵심 요약
    (3줄 이내 요약)
    
    ## 📜 법적 검토
    (법적 근거와 판단)
    
    ## 👣 조치 계획
    (구체적 해결 방안)
    
    ## 📄 답변 초안
    (민원인용 답변 텍스트)
    """
    
    callback(80, "🧠 AI 분석 및 보고서 작성 중...")
    
    # 하이브리드 엔진 호출
    res_text, source = generate_content_hybrid(prompt)
    
    if source == "Error":
        # 최후의 재시도 (Groq 한 번 더)
        time.sleep(1)
        res_text, source = generate_content_hybrid(prompt)
        if source == "Error":
            return f"죄송합니다. 시스템 접속 폭주로 분석에 실패했습니다.\n오류 내용: {res_text}", "Fail"

    callback(100, "🎉 분석 완료!")
    return res_text, source

# --- 4. UI 실행 ---

st.markdown(f"""
<div style="text-align:center; padding: 20px; background: rgba(255,255,255,0.6); border-radius: 20px; border: 1px solid rgba(255,255,255,0.4);">
    <h1 style="color:#1a237e;">⚖️ AI 행정관: The Legal Glass</h1>
    <div style="margin-top: 10px;">
        <span class="status-badge">Main: {GEMINI_MODEL_NAME}</span>
        <span class="groq-badge">Backup: Llama-3.3 (Groq)</span>
    </div>
</div>
<br>
""", unsafe_allow_html=True)

with st.container():
    st.markdown('<div style="background-color:rgba(0,0,0,0);"></div>', unsafe_allow_html=True)
    user_input = st.text_area("민원 상황 입력", height=100, placeholder="예: 층간소음으로 인한 이웃 분쟁 조정 절차가 궁금합니다.")
    btn = st.button("🚀 분석 시작", use_container_width=True, type="primary")

if btn and user_input:
    # 프로그레스 바 설정
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update_status(p, t):
        progress_bar.progress(p)
        status_text.caption(f"{t}")
        time.sleep(0.1)

    # 1. 법령 식별 및 조회
    law_name, law_text = get_law_context(user_input, update_status)
    
    # 2. 검색
    search_text = get_search_results(user_input, update_status)
    
    # 3. 최종 보고서 작성
    final_text, used_source = generate_final_report(user_input, law_name, law_text, search_text, update_status)
    
    # 완료 처리
    progress_bar.empty()
    status_text.empty()
    
    st.divider()
    
    # 엔진 사용 알림
    if used_source == "Groq":
        st.warning("⚡ Gemini 사용량 초과로 **Backup AI (Llama 3.3)**가 답변했습니다.", icon="⚡")
    elif used_source == "Fail":
        st.error(f"모든 AI 모델 연결에 실패했습니다.\n{final_text}")
    else:
        st.success(f"✨ **Gemini**가 정상적으로 분석했습니다.", icon="🤖")

    # 결과 렌더링
    sections = re.split(r'(?=## )', final_text)
    for section in sections:
        if not section.strip(): continue
        with st.container():
            st.markdown(section)

    # DB 저장
    if use_db and used_source != "Fail":
        try:
            supabase.table("law_reports").insert({
                "situation": user_input,
                "law_name": law_name,
                "summary": final_text[:500],
                "ai_model": used_source
            }).execute()
            st.toast("기록이 저장되었습니다.", icon="💾")
        except: pass

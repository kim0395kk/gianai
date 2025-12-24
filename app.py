import streamlit as st
import requests
import xml.etree.ElementTree as ET
import google.generativeai as genai
import json
import re
from urllib.parse import quote

# --- 1. 설정 및 초기화 ---
st.set_page_config(layout="wide", page_title="실무 맞춤형 행정 AI")

# [안전장치] Secrets 로드 실패 시 에러 처리
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    LAW_API_ID = st.secrets["general"]["LAW_API_ID"]
    # Supabase는 선택 사항으로 처리 (없어도 앱이 죽지 않게)
    try:
        from supabase import create_client
        SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
        SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_db = True
    except:
        use_db = False
    
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error(f"🚨 설정 오류: secrets.toml 파일을 확인해주세요. ({e})")
    st.stop()

# --- 2. 모델 자동 감지 (404 에러 방지) ---
@st.cache_data(show_spinner=False)
def get_best_model():
    """내 API 키로 사용 가능한 모델 중 최적의 모델을 찾습니다."""
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # 우선순위: Flash(빠름/저렴) -> Pro(똑똑함)
        priorities = [
            'models/gemini-1.5-flash',
            'models/gemini-1.5-flash-latest',
            'models/gemini-1.5-pro',
            'models/gemini-pro'
        ]
        
        for p in priorities:
            if p in available_models: return p
            
        return available_models[0] if available_models else None
    except:
        return None

CURRENT_MODEL = get_best_model()

# --- 3. 핵심 로직 함수들 ---

@st.cache_data(ttl=3600)
@st.cache_data(ttl=3600)
def infer_law_name(situation, model_name):
    """
    [업데이트] 상황이 복합적일 때 특정 특별법(예: 건설기계관리법)을 우선하도록 프롬프트 강화
    """
    if not model_name: return "모델 연결 실패"
    model = genai.GenerativeModel(model_name)
    
    prompt = f"""
    상황: {situation}
    
    위 상황을 규제하거나 처분할 수 있는 가장 직접적인 '대한민국 법령명' 1개만 정확히 출력해.
    
    [중요 원칙]
    1. '아파트'와 '차량/기계'가 같이 나오면 주택법보다 '도로교통법'이나 '자동차관리법', '건설기계관리법'을 우선할 것.
    2. '건설기계'(덤프, 굴착기 등)가 언급되면 무조건 '건설기계관리법'을 출력할 것.
    3. 설명 없이 법 이름만 딱 적어. (예: 건설기계관리법)
    """
    try:
        res = model.generate_content(prompt, generation_config={"max_output_tokens": 20, "temperature": 0.0})
        return res.text.strip()
    except: 
        # API 호출 실패 시, 상황에 '건설기계'가 있으면 하드코딩으로 리턴 (Fallback)
        if "건설기계" in situation:
            return "건설기계관리법"
        return "검색 실패"
def get_law_link(law_name, article_num):
    """법제처 해당 조문으로 가는 직링크 생성"""
    # URL 인코딩 (한글 처리)
    encoded_name = quote(law_name)
    # 조문 번호에서 숫자만 추출하거나 '제' 포함 형식을 맞춤
    return f"https://www.law.go.kr/법령/{encoded_name}/{article_num}"

def search_and_filter_articles(law_name, situation):
    """
    [토큰 다이어트 2단계 + 링크 생성]
    API로 조문을 긁어와서 Python으로 관련 있는 것만 추려냅니다. (AI 비용 0원)
    """
    # 1. 법령 검색 (MST 식별)
    search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={law_name}"
    try:
        res = requests.get(search_url, timeout=5)
        root = ET.fromstring(res.content)
        law_node = root.find(".//law")
        
        if law_node is None: return None, None, []
        
        mst = law_node.find("법령일련번호").text
        full_name = law_node.find("법령명한글").text
    except: return None, None, []

    # 2. 상세 조문 가져오기
    detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML"
    try:
        res = requests.get(detail_url, timeout=10)
        root = ET.fromstring(res.content)
        
        # 키워드 기반 필터링 (Python)
        keywords = situation.replace(" ", ",").split(",")
        keywords = [k for k in keywords if len(k) > 1] # 1글자 제외
        
        scored_articles = [] # (점수, 조문텍스트, 조문번호)
        
        for a in root.findall(".//조문"):
            cont = a.find('조문내용').text or ""
            num_str = a.find('조문번호').text or ""
            full_num_str = f"제{num_str}조"
            
            # 검색 점수 계산
            score = 0
            for k in keywords:
                if k in cont: score += 1
            
            # 중요 키워드 가산점 (처분, 금지, 과태료 등)
            if any(x in cont for x in ["처분", "금지", "과태료", "명령", "벌칙"]):
                score += 0.5
                
            if score > 0:
                link = get_law_link(full_name, full_num_str)
                scored_articles.append({
                    "score": score,
                    "text": f"{full_num_str}: {cont[:300]}", # 너무 길면 자름
                    "link": link,
                    "title": full_num_str
                })
        
        # 점수순 정렬 후 상위 5개만 추출
        scored_articles.sort(key=lambda x: x["score"], reverse=True)
        top_articles = scored_articles[:5]
        
        # AI에게 던져줄 텍스트 뭉치 만들기
        context_text = "\n".join([item["text"] for item in top_articles])
        
        return full_name, context_text, top_articles
        
    except Exception as e:
        print(e)
        return None, None, []

def generate_solution(situation, law_name, context, model_name):
    """[토큰 다이어트 3단계] 정제된 정보로 최종 리포트 작성"""
    model = genai.GenerativeModel(model_name)
    
    prompt = f"""
    당신은 행정 전문가입니다. 아래 정보를 바탕으로 민원 대응 보고서를 JSON 형식으로 작성하세요.
    
    [상황] {situation}
    [관련 법령: {law_name}]
    {context}
    
    [필수 포함 항목 (JSON key)]
    - summary: 법적 판단 요약 (2문장 이내)
    - steps: 단계별 처리 절차 (배열 형태, 각 단계는 'title'과 'desc'로 구성)
    - tip: 담당 공무원이 주의해야 할 실무 팁
    """
    
    try:
        # JSON 모드로 강제하여 불필요한 서론 제거
        res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(res.text)
    except: return None

# --- 4. 메인 UI 구성 ---

st.title("⚖️ 행정 업무 지능형 내비게이션")
st.caption(f"연결된 엔진: {CURRENT_MODEL if CURRENT_MODEL else '연결 불가 ❌'}")

if not CURRENT_MODEL:
    st.error("API 키를 확인하거나 잠시 후 다시 시도해주세요.")
    st.stop()

col1, col2 = st.columns([1, 1])
with col1:
    user_input = st.text_area("민원 상황 입력", height=200, placeholder="예: 아파트 단지 내 5년 방치된 거주자 차량 강제처리 가능 여부")
    analyze_btn = st.button("🚀 법령 분석 및 솔루션 확인", type="primary", use_container_width=True)

with col2:
    st.info("💡 **사용 팁**\n\n상황을 구체적으로 적을수록 정확한 법령이 매칭됩니다.\n(누가, 어디서, 무엇을, 얼마나 등)")

# --- 5. 실행 로직 ---

if analyze_btn and user_input:
    result_container = st.container()
    
    with st.status("🔍 실무 데이터 분석 중...", expanded=True) as status:
        
        # Step 1: 법령명 찾기
        status.write("1. 관련 법령 식별 중...")
        inferred_law = infer_law_name(user_input, CURRENT_MODEL)
        law_name_clean = re.sub(r'[^가-힣]', '', inferred_law)
        
        if "실패" in inferred_law:
            st.error("법령을 식별하지 못했습니다.")
            st.stop()
            
        # Step 2: 조문 추출 및 링크 생성
        status.write(f"2. [{law_name_clean}] 원문 대조 및 필터링...")
        full_name, context, articles_data = search_and_filter_articles(law_name_clean, user_input)
        
        if not context:
            st.warning(f"'{law_name_clean}'에서 관련 조문을 찾지 못했습니다. (법령명 오류 가능성)")
            # Fallback: 사용자가 직접 법령을 입력하게 할 수도 있음 (여기선 생략)
            st.stop()
            
        # Step 3: 솔루션 생성
        status.write("3. AI 솔루션 생성 중...")
        solution = generate_solution(user_input, full_name, context, CURRENT_MODEL)
        
        status.update(label="분석 완료!", state="complete")

    # --- 6. 결과 화면 출력 ---
    st.divider()
    
    # [좌측] AI 솔루션 리포트
    r_col1, r_col2 = st.columns([6, 4])
    
    with r_col1:
        st.subheader("📋 실무 가이드라인")
        if solution:
            st.success(f"**[핵심 요약]** {solution.get('summary')}")
            
            st.write("#### 👣 단계별 대응 절차")
            for idx, step in enumerate(solution.get('steps', [])):
                st.markdown(f"**{idx+1}. {step['title']}**")
                st.write(f"└ {step['desc']}")
            
            st.warning(f"💡 **실무 팁**: {solution.get('tip')}")
        else:
            st.error("솔루션 생성에 실패했습니다.")

    # [우측] 근거 법령 (다이렉트 링크 포함)
    with r_col2:
        st.subheader("📜 근거 법령 원문")
        st.caption("클릭 시 국가법령정보센터로 이동합니다.")
        
        for item in articles_data:
            # 클릭 가능한 버튼/링크 형태로 표시
            st.markdown(f"""
            <div style="padding:10px; border:1px solid #ddd; border-radius:5px; margin-bottom:10px;">
                <a href="{item['link']}" target="_blank" style="text-decoration:none; color:#0056b3; font-weight:bold;">
                    🔗 {full_name} {item['title']}
                </a>
                <p style="font-size:13px; color:#555; margin-top:5px;">
                    {item['text'][:80]}...
                </p>
            </div>
            """, unsafe_allow_html=True)

    # --- 7. DB 저장 (옵션) ---
    if use_db and solution:
        try:
            supabase.table("law_reports").insert({
                "situation": user_input,
                "law_name": full_name,
                "summary": solution.get('summary'),
                "tip": solution.get('tip')
            }).execute()
        except Exception:
            pass # DB 에러는 사용자에게 안 보이게 처리


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

# --- 0. 페이지 설정 및 디자인 ---
st.set_page_config(layout="wide", page_title="AI 행정관: The Legal Glass", page_icon="⚖️")

st.markdown("""
<style>
    .stApp { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); }
    div[data-testid="stVerticalBlock"] > div[style*="background-color"] {
        background: rgba(255, 255, 255, 0.95);
        box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1);
        backdrop-filter: blur(8px);
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.4);
        padding: 25px;
        margin-bottom: 20px;
    }
    h1, h2, h3 { color: #1a237e !important; font-family: 'Pretendard', sans-serif; }
    strong { color: #1a237e; background-color: rgba(26, 35, 126, 0.1); padding: 2px 4px; border-radius: 4px; }
    .status-badge { background-color: #dbeafe; color: #1e40af; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; }
    .groq-badge { background-color: #fce7f3; color: #9d174d; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; border: 1px solid #fbcfe8; }
    /* A2UI 영역 스타일 */
    .a2ui-header { color: #4338ca; font-weight: bold; font-size: 1.2rem; margin-bottom: 10px; display: flex; align-items: center; }
</style>
""", unsafe_allow_html=True)

# --- 1. API 연결 및 예외처리 ---
try:
    # Streamlit Secrets에서 키 가져오기
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
    st.error(f"🚨 시스템 설정 오류 (Secrets를 확인하세요): {e}")
    st.stop()

# 모델 우선순위 설정
GEMINI_PRIORITY_LIST = ["gemini-2.0-flash-exp", "gemini-1.5-flash", "gemini-1.5-pro"]
GROQ_MODEL = "llama-3.3-70b-versatile"

# --- 2. 하이브리드 LLM 엔진 ---
def generate_content_hybrid(prompt, temp=0.1):
    """Gemini 시도 후 실패 시 Groq(Llama 3.3)로 전환"""
    # 1. Gemini 시도
    for model_name in GEMINI_PRIORITY_LIST:
        try:
            model = genai.GenerativeModel(model_name)
            res = model.generate_content(prompt, request_options={'timeout': 15})
            return res.text, f"Gemini ({model_name})"
        except Exception:
            continue

    # 2. Groq 시도 (Fallback)
    if groq_client:
        try:
            system_role = "당신은 대한민국 최고의 행정법 전문 변호사이자 UI/UX 설계를 돕는 AI 에이전트입니다. 논리적이고 실용적인 답변을 제공하세요."
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
        return "모든 AI 모델 연결 실패", "Fail"

# --- 3. 법령 데이터 처리 (Atomic Logic) ---

def get_relevant_articles(detail_root, situation):
    """상황에 맞는 조문만 필터링하여 토큰 절약"""
    mapping_keywords = ["금지", "관리", "처분", "과태료", "벌칙", "의무", "안전", "제1조"]
    
    # 상황별 동적 키워드 추가
    if any(x in situation for x in ["킥보드", "자전거", "이동장치"]):
        mapping_keywords.extend(["통행", "장애", "적치", "이동", "도로"])
    if "주차" in situation: mapping_keywords.extend(["주차", "교통", "방해", "견인"])
    if "소음" in situation: mapping_keywords.extend(["소음", "진동", "환경", "차음"])
    if "아파트" in situation: mapping_keywords.extend(["입주자", "관리주체", "공용", "전유"])
    if "기초수급" in situation or "급여" in situation: mapping_keywords.extend(["부양", "소득", "인정", "기준"])

    filtered_articles = []
    for a in detail_root.findall(".//조문"):
        num = a.find('조문번호').text or ""
        cont = a.find('조문내용').text or ""
        full_text = cont
        sub_clauses = []
        for sub in a.findall(".//항"):
            s_num = sub.find('항번호').text or ""
            s_cont = sub.find('항내용').text or ""
            full_text += f" {s_cont}"
            sub_clauses.append(f"  ({s_num}) {s_cont}")
            
        if any(kw in full_text for kw in mapping_keywords):
            filtered_articles.append(f"[제{num}조] {cont}\n" + "\n".join(sub_clauses))
            
    # 필터링 결과가 적으면 앞부분 기본 조항 가져옴
    if len(filtered_articles) < 3:
        for a in detail_root.findall(".//조문")[:20]:
            filtered_articles.append(f"[제{a.find('조문번호').text}조] {a.find('조문내용').text}")
    return filtered_articles

def search_candidates_from_api(keywords):
    candidates = set()
    for kw in keywords:
        if len(kw) < 2: continue
        try:
            url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={kw}&display=3"
            res = requests.get(url, timeout=3)
            root = ET.fromstring(res.content)
            for law in root.findall(".//law"):
                candidates.add(law.find("법령명한글").text)
        except: continue
    return list(candidates)

def get_law_context_advanced(situation, callback):
    """[Reasoning -> Action -> Filtering] 파이프라인"""
    callback(10, "🤔 쟁점 분석 및 키워드 추출 중...")
    
    prompt_kw = f"상황: {situation}\n관련 법령 검색 키워드 3개를 JSON으로 추출해. {{ \"keywords\": [\"단어1\", \"단어2\"] }}"
    keywords_json, _ = generate_content_hybrid(prompt_kw)
    try:
        keywords = json.loads(re.search(r'\{.*\}', keywords_json, re.DOTALL).group()).get("keywords", ["행정"])
    except: keywords = ["행정", "민원"]

    callback(30, f"🔎 검색어: {', '.join(keywords)}")
    candidates = search_candidates_from_api(keywords)
    if not candidates: candidates = ["민법", "도로교통법"] # Fallback

    callback(50, f"⚖️ 최적 법령 선별 중... (후보: {len(candidates)}개)")
    prompt_sel = f"상황: {situation}\n후보: {', '.join(candidates)}\n가장 적합한 법령 1개 이름만 출력."
    best_law_name, _ = generate_content_hybrid(prompt_sel)
    best_law_name = re.sub(r"[\"'\[\]]", "", best_law_name).strip()
    
    final_name = next((cand for cand in candidates if cand in best_law_name), candidates[0])
    
    callback(70, f"📜 '{final_name}' 조항 분석 중...")
    try:
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_ID}&target=law&type=XML&query={final_name}"
        root = ET.fromstring(requests.get(search_url).content)
        mst = root.find(".//MST").text
        
        detail_res = requests.get(f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_ID}&target=law&MST={mst}&type=XML")
        detail_root = ET.fromstring(detail_res.content)
        articles = get_relevant_articles(detail_root, situation)
        return final_name, "\n".join(articles)
    except Exception as e:
        return final_name, "데이터 로드 실패. AI 지식으로 대체합니다."

def get_search_results(situation, callback):
    callback(80, "🔍 관련 판례 및 행정 사례 검색 중...")
    try:
        params = {"engine": "google", "q": f"{situation} 판례 행정처분", "api_key": SERPAPI_KEY, "num": 2}
        results = GoogleSearch(params).get_dict().get("organic_results", [])
        return "\n".join([f"- {item['title']}: {item['snippet']}" for item in results])
    except: return "(검색 결과 없음)"

# --- 4. A2UI 기반 보고서 생성 (The Core) ---

def generate_report_with_a2ui(situation, law_name, law_text, search_text, callback):
    """텍스트 답변 + UI JSON 생성"""
    
    prompt = f"""
    당신은 유능한 'AI 행정관'입니다. 법률적 조언과 함께 사용자가 바로 행동할 수 있는 도구를 제공하세요.
    
    [민원 내용] {situation}
    [적용 법령: {law_name}]
    
    [법령 데이터 Context]
    {law_text[:10000]} 
    
    [지시사항]
    1. 법령과 판례에 근거하여 명확하고 친절한 답변을 작성하세요. (마크다운 포맷)
    2. **필수:** 사용자가 문서를 작성하거나, 신고하거나, 체크해야 할 사항이 있다면 답변 맨 끝에 **JSON 포맷**으로 UI 데이터를 생성하세요.
    
    [A2UI JSON 규격 및 예시]
    반드시 아래 포맷 중 하나를 선택하여 ```json ... ``` 블록으로 감싸서 출력하세요.
    
    Type A: 문서 작성기 (doc_builder)
    ```json
    {{
      "a2ui_type": "doc_builder",
      "title": "내용증명/신고서 자동 작성",
      "description": "아래 정보를 입력하시면 즉시 초안을 만들어 드립니다.",
      "fields": [
        {{"id": "name", "label": "수신인 이름", "type": "text"}},
        {{"id": "reason", "label": "신고 사유", "type": "text"}}
      ],
      "template": "수신: [name]\\n내용: [reason]로 인해 시정 조치를 요청합니다."
    }}
    ```
    
    Type B: 정보 카드 (info_card)
    ```json
    {{
      "a2ui_type": "info_card",
      "title": "관련 신고 접수처",
      "buttons": [
        {{"label": "국민신문고 바로가기", "url": "[https://www.epeople.go.kr](https://www.epeople.go.kr)"}}
      ]
    }}
    ```
    """
    
    callback(90, "🧠 심층 분석 및 A2UI 컴포넌트 설계 중...")
    res, source = generate_content_hybrid(prompt, temp=0.2)
    callback(100, "완료!")
    return res, source

# --- 5. A2UI 렌더러 (UI 생성 엔진) ---

def render_a2ui_component(full_text):
    """텍스트에서 JSON을 분리하고 Streamlit 위젯을 그림"""
    
    # 1. JSON 블록 추출
    json_pattern = r'```json\s*(\{.*?\})\s*```'
    match = re.search(json_pattern, full_text, re.DOTALL)
    
    # 텍스트 부분만 리턴 (화면에 출력용)
    display_text = re.sub(json_pattern, '', full_text, flags=re.DOTALL).strip()
    
    # JSON이 없으면 텍스트만 표시하고 종료
    if not match:
        st.markdown(display_text)
        return

    # JSON이 있으면 렌더링 진행
    st.markdown(display_text)
    
    try:
        data = json.loads(match.group(1))
        
        st.divider()
        st.markdown(f"<div class='a2ui-header'>⚡ AI Action Center: {data.get('title', '추천 액션')}</div>", unsafe_allow_html=True)
        
        # [Case 1] 문서 작성기
        if data.get("a2ui_type") == "doc_builder":
            with st.container(border=True):
                st.info(data.get("description", "정보를 입력하면 문서가 생성됩니다."))
                inputs = {}
                with st.form("a2ui_form"):
                    # 동적 필드 생성
                    for field in data.get("fields", []):
                        inputs[field["id"]] = st.text_input(field["label"])
                    
                    submitted = st.form_submit_button("📄 문서 생성하기", type="primary")
                
                if submitted:
                    template = data.get("template", "")
                    for key, val in inputs.items():
                        template = template.replace(f"[{key}]", val)
                    st.success("✅ 문서 초안이 완성되었습니다.")
                    st.code(template, language="text")

        # [Case 2] 정보/링크 카드
        elif data.get("a2ui_type") == "info_card":
            with st.container(border=True):
                cols = st.columns(len(data.get("buttons", [])))
                for idx, btn in enumerate(data.get("buttons", [])):
                    with cols[idx]:
                        st.link_button(btn["label"], btn["url"], use_container_width=True)

        # [Case 3] 체크리스트
        elif data.get("a2ui_type") == "checklist":
            with st.container(border=True):
                st.write(data.get("description", "다음 절차를 확인하세요."))
                for item in data.get("items", []):
                    st.checkbox(item)

    except json.JSONDecodeError:
        pass # JSON 파싱 에러 시 UI 렌더링 생략
    except Exception as e:
        st.error(f"UI 렌더링 오류: {e}")

# --- 6. 메인 실행 루프 ---

st.markdown("""
<div style="text-align:center; padding: 20px;">
    <h1 style="color:#1a237e;">⚖️ AI 행정관: The Legal Glass</h1>
    <p style="color:#666;">법률 분석부터 문서 작성까지, 행동하는 AI 에이전트</p>
    <div>
        <span class="status-badge">Main: Gemini</span>
        <span class="groq-badge">Backup: Groq</span>
    </div>
</div>
""", unsafe_allow_html=True)

with st.container():
    user_input = st.text_area("법률적 도움이 필요한 상황을 입력하세요", height=100, placeholder="예: 윗집 층간소음 때문에 내용증명을 보내고 싶어요. / 기초수급자인데 자녀 때문에 탈락했어요.")
    btn = st.button("🚀 분석 및 솔루션 실행", type="primary", use_container_width=True)

if btn and user_input:
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update_status(p, t):
        progress_bar.progress(p)
        status_text.caption(f"{t}")
        time.sleep(0.05)

    # 1. 법령 및 검색
    law_name, law_text = get_law_context_advanced(user_input, update_status)
    search_text = get_search_results(user_input, update_status)
    
    # 2. A2UI 보고서 생성
    final_response, used_source = generate_report_with_a2ui(user_input, law_name, law_text, search_text, update_status)
    
    progress_bar.empty()
    status_text.empty()
    
    # 3. 메타 정보 표시
    if used_source != "Fail":
        st.success(f"✨ Analysis by **{used_source}** | 법령: {law_name}", icon="🤖")
        
        # 4. 결과 및 A2UI 렌더링 (핵심)
        render_a2ui_component(final_response)
        
        # DB 저장
        if use_db:
            try:
                supabase.table("law_reports").insert({
                    "situation": user_input,
                    "law_name": law_name,
                    "summary": final_response[:500],
                    "ai_model": used_source
                }).execute()
            except: pass
    else:
        st.error("분석에 실패했습니다. 잠시 후 다시 시도해주세요.")

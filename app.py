import streamlit as st
import google.generativeai as genai
from groq import Groq
from serpapi import GoogleSearch
from supabase import create_client
import json, re, time
from datetime import datetime, timedelta

# ==========================================
# 1. 디자인 및 스타일
# ==========================================
st.set_page_config(layout="wide", page_title="AI Bureau: The Legal Glass", page_icon="⚖️")
st.markdown("""
<style>
    .stApp { background-color: #f3f4f6; }
    .paper-sheet { background: white; padding: 25mm; margin: auto; box-shadow: 0 10px 30px rgba(0,0,0,0.1); font-family: 'Batang', serif; position: relative; }
    .doc-header { text-align: center; font-size: 22pt; font-weight: 900; margin-bottom: 30px; }
    .doc-info { display: flex; justify-content: space-between; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 20px; }
    .doc-body { font-size: 12pt; text-align: justify; white-space: pre-line; }
    .doc-footer { text-align: center; font-size: 20pt; font-weight: bold; margin-top: 80px; }
    .stamp { position: absolute; bottom: 85px; right: 80px; border: 3px solid #cc0000; color: #cc0000; padding: 5px 10px; font-weight: bold; transform: rotate(-15deg); border-radius: 5px; }
    .agent-log { font-family: 'Consolas', monospace; font-size: 0.85rem; padding: 8px; border-radius: 6px; margin-bottom: 5px; border-left: 4px solid #ddd; background: white; }
    .log-legal { border-color: #3b82f6; color: #1e40af; } 
    .log-search { border-color: #f97316; color: #c2410c; }
    .log-strat { border-color: #8b5cf6; color: #6d28d9; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 인프라 레이어 (LLM, Search, DB)
# ==========================================

class LLMService:
    def __init__(self):
        self.gemini_models = ["gemini-3-flash", "gemini-2.5-flash", "gemini-2.0-flash-lite"]
        if st.secrets.get("general", {}).get("GEMINI_API_KEY"):
            genai.configure(api_key=st.secrets["general"]["GEMINI_API_KEY"])
        self.groq_client = Groq(api_key=st.secrets["general"]["GROQ_API_KEY"]) if st.secrets.get("general", {}).get("GROQ_API_KEY") else None

    def _try_gemini(self, prompt, is_json=False):
        for model_name in self.gemini_models:
            try:
                model = genai.GenerativeModel(model_name)
                config = genai.GenerationConfig(response_mime_type="application/json") if is_json else None
                res = model.generate_content(prompt, generation_config=config)
                return res.text, model_name
            except: continue
        raise Exception("All Gemini models failed")

    def generate_text(self, prompt):
        try: return self._try_gemini(prompt, False)
        except: 
            if self.groq_client:
                res = self.groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": prompt}]).choices[0].message.content
                return res, "Groq(Llama-3.3)"
            return "연결 실패", "None"

    def generate_json(self, prompt):
        try:
            text, model = self._try_gemini(prompt, True)
            return json.loads(text), model
        except:
            text, model = self.generate_text(prompt + "\nOutput strictly in JSON { ... }")
            match = re.search(r'\{.*\}', text, re.DOTALL)
            return (json.loads(match.group(0)) if match else None), "Fallback/Groq"

class SearchService:
    """[복구 완료] Google Search API (SerpApi) Wrapper"""
    def __init__(self):
        self.api_key = st.secrets["general"].get("SERPAPI_KEY")

    def search_precedents(self, query):
        if not self.api_key:
            return "⚠️ 검색 API 키(SERPAPI_KEY)가 없어 유사 사례를 조회할 수 없습니다."
        try:
            search_query = f"{query} 행정처분 판례 사례 민원 답변"
            params = {"engine": "google", "q": search_query, "api_key": self.api_key, "num": 3, "hl": "ko", "gl": "kr"}
            search = GoogleSearch(params)
            results = search.get_dict().get("organic_results", [])
            if not results: return "관련된 유사 사례 검색 결과가 없습니다."
            summary = [f"- **[{item.get('title')}]({item.get('link')})**: {item.get('snippet')}" for item in results]
            return "\n".join(summary)
        except Exception as e: return f"검색 중 오류 발생: {e}"

class DatabaseService:
    def __init__(self):
        try:
            self.client = create_client(st.secrets["supabase"]["SUPABASE_URL"], st.secrets["supabase"]["SUPABASE_KEY"])
            self.is_active = True
        except: self.is_active = False

    def get_usage_stats(self):
        if not self.is_active: return 0, 0
        try:
            total = self.client.table("law_reports").select("id", count="exact").execute().count
            today = datetime.now().strftime('%Y-%m-%d')
            today_cnt = self.client.table("law_reports").select("id", count="exact").gte("created_at", f"{today}T00:00:00").execute().count
            return today_cnt or 0, total or 0
        except: return 0, 0

    def save_log(self, situation, law, strategy, doc):
        if not self.is_active: return "DB 미연결"
        try:
            self.client.table("law_reports").insert({"situation": situation, "law_name": law, "summary": json.dumps({"strat": strategy, "doc": doc}, ensure_ascii=False)}).execute()
            return "저장 성공"
        except Exception as e: return f"실패: {e}"

llm_service, search_service, db_service = LLMService(), SearchService(), DatabaseService()

# ==========================================
# 3. 도메인 레이어 (SPL 프롬프트 보존 구역)
# ==========================================
class LegalAgents:
    @staticmethod
    def researcher(situation):
    """Step 1: 법령 탐색 (가중치 순 최대 3개)"""
    prompt = f"""
        Role: 당신은 대한민국 최고의 행정 법률 전문가입니다.
        Task: 아래 상황에 적용될 법령명과 조항 번호를 정확히 찾아 설명하세요.
        
        [출력 제약사항 - 매우 중요]
        1. 당신이 누구인지(예: "30년 경력 전문가로서...") 절대 말하지 마세요.
        2. 인삿말이나 사족 없이, **바로 법령명과 내용부터** 출력하세요.
        3. 말투는 정중하고 건조한 행정보고서 스타일을 유지하세요.
        
        <instruction>
        상황: "{situation}"
        위 상황에 적용 가능한 법령을 **상황과의 밀접성(가중치)이 높은 순서대로 최대 3개**를 찾으시오.
        반드시 현행 대한민국 법령이어야 하며, 각 조항별로 선택한 이유(적용 근거)를 한 문장으로 요약하여 덧붙이시오.
        
        [출력 형식]
        1. 법령명 제00조(조항제목): (적용 근거)
        2. 법령명 제00조(조항제목): (적용 근거)
        
        *주의: 입력에 실명 등 개인정보가 있다면 마스킹하여 처리하세요.
        </instruction>
        """"
        return llm_service.generate_text(prompt)

    @staticmethod
    def strategist(situation, legal_basis, search_results):
        prompt = f"""
        당신은 행정 업무 베테랑 '주무관'입니다.
        [민원 상황]: {situation} / [법적 근거]: {legal_basis} / [유사 사례]: {search_results}
        위 정보를 종합하여 마크다운으로 작성하세요: 1. 처리 방향 2. 핵심 주의사항 3. 예상 반발 및 대응
        """
        return llm_service.generate_text(prompt)

    @staticmethod
    def drafter(situation, law, meta, strategy):
        prompt = f"기안문 작성. 상황:{situation}, 법:{law}, 전략:{strategy}. 시행일:{meta['today_str']}. JSON{{title, receiver, body_paragraphs[], department_head}} 반환."
        return llm_service.generate_json(prompt)

# ==========================================
# 4. 워크플로우 (인자 전달 및 튜플 언패킹 수정 완료)
# ==========================================
def run_workflow(user_input, dept, officer):
    log_placeholder, model_usage = st.empty(), {}
    st.session_state.logs = [] 

    def add_log(msg, style="sys"):
        st.session_state.logs.append(f"<div class='agent-log log-{style}'>{msg}</div>")
        log_placeholder.markdown("".join(st.session_state.logs), unsafe_allow_html=True)
        time.sleep(0.1)

    # Phase 1: Research
    add_log("🔍 Phase 1: 법령 리서치 중...", "legal")
    law_text, m1 = LegalAgents.researcher(user_input)
    model_usage['리서치'] = m1
    
    # Phase 1-2: Google Search [복구된 클래스 사용]
    add_log("🌍 구글 검색 엔진 가동 (유사 사례 수집)...", "search")
    search_res = search_service.search_precedents(user_input)

    # Phase 2: Strategy (검색 결과 전달)
    add_log("🧠 Phase 2: 업무 전략 수립...", "strat")
    strat_text, m2 = LegalAgents.strategist(user_input, law_text, search_res)
    model_usage['전략'] = m2

    # Phase 3: Drafting
    add_log("✍️ Phase 3: 공문서 작성 중...", "sys")
    today = datetime.now()
    meta = {"today_str": today.strftime("%Y. %m. %d."), "doc_num": f"행정-{today.year}-{int(time.time())%1000:03d}호"}
    doc_data, m3 = LegalAgents.drafter(user_input, law_text, meta, strat_text)
    model_usage['작성'] = m3

    # Step 4: DB 저장 및 통계
    save_msg = db_service.save_log(user_input, law_text, strat_text, doc_data)
    tokens = int(len(user_input + law_text + strat_text + str(doc_data)) * 1.5)
    
    log_placeholder.empty()
    return {"doc": doc_data, "meta": meta, "law": law_text, "search": search_res, "strat": strat_text, "model_usage": model_usage, "tokens": tokens, "save_msg": save_msg}

# ==========================================
# 5. UI 메인 레이아웃
# ==========================================
def main():
    st.session_state.setdefault("dept", "충주시청 ○○과"); st.session_state.setdefault("officer", "이주무관")
    col_l, col_r = st.columns([1, 1.2])

    with col_l:
        st.title("🏢 AI 행정관 Pro")
        with st.expander("👤 담당자 설정"):
            st.text_input("부서", key="dept"); st.text_input("이름", key="officer")
        user_input = st.text_area("업무 지시", height=150, placeholder="민원 내용을 입력하세요.")
        
        if st.button("🚀 실행", type="primary", use_container_width=True):
            if not user_input: st.warning("내용을 입력하세요.")
            else: st.session_state['res'] = run_workflow(user_input, st.session_state.dept, st.session_state.officer)

        if 'res' in st.session_state:
            res = st.session_state['res']
            st.markdown("---")
            today_cnt, total_cnt = db_service.get_usage_stats()
            c1, c2, c3 = st.columns(3)
            c1.metric("이번 토큰", f"{res['tokens']:,}"); c2.metric("오늘 처리", f"{today_cnt}건"); c3.metric("누적 처리", f"{total_cnt}건")
            
            with st.expander("📊 작업 모델 추적", expanded=True):
                for step, model in res['model_usage'].items(): st.write(f"**{step}**: {model}")
            with st.expander("🌍 유사 사례 결과", expanded=False): st.info(res['search'])

    with col_r:
        if 'res' in st.session_state:
            res = st.session_state['res']
            doc, meta = res['doc'], res['meta']
            st.markdown(f"""
            <div class="paper-sheet">
                <div class="stamp">직인생략</div>
                <div class="doc-header">{doc.get('title')}</div>
                <div class="doc-info"><span>번호: {meta['doc_num']}</span><span>일자: {meta['today_str']}</span><span>수신: {doc.get('receiver')}</span></div>
                <div class="doc-body">{"".join([f"<p>{p}</p>" for p in doc.get('body_paragraphs', [])])}</div>
                <div class="doc-footer">{doc.get('department_head')}</div>
            </div>
            """, unsafe_allow_html=True)

if __name__ == "__main__": main()

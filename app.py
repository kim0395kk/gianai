import streamlit as st
import google.generativeai as genai
from groq import Groq
from serpapi import GoogleSearch
from supabase import create_client
import json
import re
import time
from datetime import datetime, timedelta

# ==========================================
# 1. Configuration & Styles (설정 및 디자인)
# ==========================================
st.set_page_config(layout="wide", page_title="AI Bureau: The Legal Glass", page_icon="⚖️")

st.markdown("""
<style>
    /* 배경: 차분한 오피스 톤 */
    .stApp { background-color: #f3f4f6; }
    
    /* 결과물: A4 용지 스타일 */
    .paper-sheet {
        background-color: white;
        width: 100%;
        max-width: 210mm;
        min-height: 297mm;
        padding: 25mm;
        margin: auto;
        box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        font-family: 'Batang', serif;
        color: #111;
        line-height: 1.6;
        position: relative;
    }
    
    /* 공문서 내부 스타일 */
    .doc-header { text-align: center; font-size: 22pt; font-weight: 900; margin-bottom: 30px; letter-spacing: 2px; }
    .doc-info { display: flex; justify-content: space-between; font-size: 11pt; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 20px; }
    .doc-body { font-size: 12pt; text-align: justify; white-space: pre-line; }
    .doc-footer { text-align: center; font-size: 20pt; font-weight: bold; margin-top: 80px; letter-spacing: 5px; }
    .stamp { position: absolute; bottom: 85px; right: 80px; border: 3px solid #cc0000; color: #cc0000; padding: 5px 10px; font-size: 14pt; font-weight: bold; transform: rotate(-15deg); opacity: 0.8; border-radius: 5px; }
    
    /* 로그 스타일 */
    .agent-log { font-family: 'Consolas', monospace; font-size: 0.85rem; padding: 6px 12px; border-radius: 6px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
    .log-legal { background-color: #eff6ff; color: #1e40af; border-left: 4px solid #3b82f6; } /* Blue */
    .log-search { background-color: #fff7ed; color: #c2410c; border-left: 4px solid #f97316; } /* Orange */
    .log-strat { background-color: #f5f3ff; color: #6d28d9; border-left: 4px solid #8b5cf6; } /* Purple */
    .log-calc { background-color: #f0fdf4; color: #166534; border-left: 4px solid #22c55e; } /* Green */
    .log-draft { background-color: #fef2f2; color: #991b1b; border-left: 4px solid #ef4444; } /* Red */
    .log-sys { background-color: #f3f4f6; color: #4b5563; border-left: 4px solid #9ca3af; } /* Gray */
    
    /* 전략 박스 스타일 */
    .strategy-box { background-color: #fffbeb; border: 1px solid #fcd34d; padding: 15px; border-radius: 8px; margin-bottom: 15px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Infrastructure Layer (Services)
# ==========================================

class LLMService:
    """
    [Model Hierarchy]
    1. Gemini 2.5 Flash
    2. Gemini 2.5 Flash Lite
    3. Gemini 2.0 Flash
    4. Groq (Llama 3 Backup)
    """
    def __init__(self):
        self.gemini_key = st.secrets["general"].get("GEMINI_API_KEY")
        self.groq_key = st.secrets["general"].get("GROQ_API_KEY")
        
        # [선생님 요청사항] 모델 리스트 원상복구 (2.5 포함)
        self.gemini_models = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash"
        ]
        
        if self.gemini_key:
            genai.configure(api_key=self.gemini_key)
            
        self.groq_client = Groq(api_key=self.groq_key) if self.groq_key else None

    def _try_gemini(self, prompt, is_json=False, schema=None):
        for model_name in self.gemini_models:
            try:
                # 모델 호출 (대소문자 이슈 방지 위해 lower 처리 등은 상황에 맞게)
                model = genai.GenerativeModel(model_name)
                config = genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=schema
                ) if is_json else None
                
                res = model.generate_content(prompt, generation_config=config)
                return res.text, model_name
            except Exception:
                continue # 다음 모델 시도
        raise Exception("All Gemini models failed")

    def generate_text(self, prompt):
        try:
            text, model_used = self._try_gemini(prompt, is_json=False)
            return text
        except Exception:
            if self.groq_client:
                return self._generate_groq(prompt)
            return "시스템 오류: AI 모델 연결 실패"

    def generate_json(self, prompt, schema=None):
        try:
            text, model_used = self._try_gemini(prompt, is_json=True, schema=schema)
            return json.loads(text)
        except Exception:
            # Fallback for Groq or Gemini without JSON mode
            text = self.generate_text(prompt + "\n\nOutput strictly in JSON.")
            try:
                match = re.search(r'\{.*\}', text, re.DOTALL)
                return json.loads(match.group(0)) if match else None
            except:
                return None

    def _generate_groq(self, prompt):
        try:
            completion = self.groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            return completion.choices[0].message.content
        except:
            return "System Error"

class SearchService:
    """Google Search API (SerpApi) Wrapper"""
    def __init__(self):
        self.api_key = st.secrets["general"].get("SERPAPI_KEY")

    def search_precedents(self, query):
        if not self.api_key:
            return "⚠️ 검색 API 키(SERPAPI_KEY)가 없어 유사 사례를 조회할 수 없습니다."
        
        try:
            search_query = f"{query} 행정처분 판례 사례 민원 답변"
            params = {
                "engine": "google",
                "q": search_query,
                "api_key": self.api_key,
                "num": 3,
                "hl": "ko",
                "gl": "kr"
            }
            search = GoogleSearch(params)
            results = search.get_dict().get("organic_results", [])
            
            if not results:
                return "관련된 유사 사례 검색 결과가 없습니다."

            summary = []
            for item in results:
                title = item.get('title', '제목 없음')
                snippet = item.get('snippet', '내용 없음')
                link = item.get('link', '#')
                summary.append(f"- **[{title}]({link})**: {snippet}")
            
            return "\n".join(summary)
        except Exception as e:
            return f"검색 중 오류 발생: {e}"

class DatabaseService:
    """Supabase Persistence Layer"""
    def __init__(self):
        try:
            # streamlit secrets에 SUPABASE_URL, SUPABASE_KEY가 있어야 합니다.
            self.url = st.secrets["supabase"]["SUPABASE_URL"]
            self.key = st.secrets["supabase"]["SUPABASE_KEY"]
            self.client = create_client(self.url, self.key)
            self.is_active = True
        except Exception:
            self.is_active = False

    def save_log(self, user_input, legal_basis, strategy, doc_data):
        if not self.is_active:
            return "DB 미연결 (저장 건너뜀)"
            
        try:
            # summary 컬럼에 '전략'과 '최종 문서 내용'을 합쳐서 JSON 텍스트로 저장
            final_summary_content = {
                "strategy": strategy,
                "document_content": doc_data
            }
            
            data = {
                "situation": user_input,      
                "law_name": legal_basis,      
                "summary": json.dumps(final_summary_content, ensure_ascii=False) 
            }

            self.client.table("law_reports").insert(data).execute()
            
            return "DB 저장 성공"
        except Exception as e:
            return f"DB 저장 실패: {e}"

# 싱글톤 인스턴스 생성
llm_service = LLMService()
search_service = SearchService()
db_service = DatabaseService()

# ==========================================
# 3. Domain Layer (Agents)
# ==========================================
class LegalAgents:
    @staticmethod
    def researcher(situation):
        """Step 1: 법령 탐색"""
        prompt = f"""
        Role: 당신은 대한민국 최고의 행정 법률 전문가입니다.
        Task: 아래 상황에 적용될 법령명과 조항 번호를 정확히 찾아 설명하세요.
        
        [출력 제약사항 - 매우 중요]
        1. 당신이 누구인지(예: "30년 경력 전문가로서...") 절대 말하지 마세요.
        2. 인삿말이나 사족 없이, **바로 법령명과 내용부터** 출력하세요.
        3. 말투는 정중하고 건조한 행정보고서 스타일을 유지하세요.
        <instruction>
        상황: "{situation}"
        위 상황에 적용할 가장 정확한 '법령명'과 '관련 조항'을 하나만 찾으시오.
        반드시 현행 대한민국 법령이어야 하며, 조항 번호까지 명시하세요.
        (예: 도로교통법 제32조(정차 및 주차의 금지))
        
        *주의: 입력에 실명 등 개인정보가 있다면 마스킹하여 처리하세요.
        </instruction>
        """
        return llm_service.generate_text(prompt).strip()

    @staticmethod
    def strategist(situation, legal_basis, search_results):
        """Step 2: 전략 수립"""
        prompt = f"""
        당신은 행정 업무 베테랑 '주무관'입니다.
        
        [민원 상황]: {situation}
        [법적 근거]: {legal_basis}
        [유사 사례/판례]: {search_results}
        
        위 정보를 종합하여 이 민원을 처리하기 위한 **대략적인 업무 처리 방향(Strategy)**을 수립하세요.
        
        다음 3가지 항목을 포함하여 마크다운으로 작성하세요:
        1. **처리 방향**: (예: 강경 대응, 계도 위주, 반려 등)
        2. **핵심 주의사항**: (절차상 놓치면 안 되는 것, 법적 쟁점)
        3. **예상 반발 및 대응**: (민원인이 항의할 경우 대응 논리)
        
        간결하고 명확하게 작성하세요.
        """
        return llm_service.generate_text(prompt)

    @staticmethod
    def clerk(situation, legal_basis):
        """Step 3: 기한 산정"""
        today = datetime.now()
        prompt = f"""
        오늘: {today.strftime('%Y-%m-%d')}
        상황: {situation}
        법령: {legal_basis}
        위 상황에서 행정처분 사전통지나 이행 명령 시, 법적으로(또는 통상적으로) 부여해야 하는 '이행/의견제출 기간'은 며칠인가?
        설명 없이 숫자(일수)만 출력하세요. (예: 10, 15, 20)
        모르겠으면 15를 출력하세요.
        """
        try:
            res = llm_service.generate_text(prompt)
            days = int(re.sub(r'[^0-9]', '', res))
        except:
            days = 15
        deadline = today + timedelta(days=days)
        return {
            "today_str": today.strftime("%Y. %m. %d."),
            "deadline_str": deadline.strftime("%Y. %m. %d."),
            "days_added": days,
            "doc_num": f"행정-{today.strftime('%Y')}-{int(time.time())%1000:03d}호"
        }

    @staticmethod
    def drafter(situation, legal_basis, meta_info, strategy):
        """Step 4: 공문서 작성"""
        doc_schema = {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "공문서 제목"},
                "receiver": {"type": "STRING", "description": "수신인"},
                "body_paragraphs": {"type": "ARRAY", "items": {"type": "STRING"}},
                "department_head": {"type": "STRING", "description": "발신 명의"}
            },
            "required": ["title", "receiver", "body_paragraphs", "department_head"]
        }
        
        prompt = f"""
        당신은 행정기관의 베테랑 서기입니다. 아래 정보를 바탕으로 완결된 공문서를 작성하세요.
        
        [입력 정보]
        - 민원 상황: {situation}
        - 법적 근거: {legal_basis}
        - 시행 일자: {meta_info['today_str']}
        - 기한: {meta_info['deadline_str']} ({meta_info['days_added']}일)
        
        [업무 처리 가이드라인 (전략)]
        {strategy}
        
        [작성 원칙]
        1. 위 '업무 처리 가이드라인'의 기조를 반영하여 어조를 결정하세요.
        2. 수신인이 불명확하면 상황에 맞춰 추론하세요.
        3. 본문 구조: [경위] -> [근거] -> [처분 내용] -> [권리구제 절차]
        4. 개인정보(이름, 번호)는 반드시 마스킹('OOO') 처리하세요.
        """
        return llm_service.generate_json(prompt, schema=doc_schema)

# ==========================================
# 4. Workflow (UI 로직 - 버그 수정판)
# ==========================================
def run_workflow(user_input):
    # 1. 로그가 출력될 공간
    log_placeholder = st.empty()
    logs = []
    
    def add_log(msg, style="sys"):
        logs.append(f"<div class='agent-log log-{style}'>{msg}</div>")
        log_placeholder.markdown("".join(logs), unsafe_allow_html=True)
        time.sleep(0.3)

    # ----------------------------------------
    # Phase 1: Fact Check & Research
    # ----------------------------------------
    add_log("🔍 Phase 1: 법령 및 유사 사례 리서치 중...", "legal")
    
    # [수정] Agents -> LegalAgents (클래스 이름 통일)
    legal_basis = LegalAgents.researcher(user_input)
    add_log(f"📜 법적 근거 발견 완료", "legal")
    
    add_log("🌍 구글 검색 엔진 가동...", "search")
    try:
        search_results = search_service.search_precedents(user_input)
    except:
        search_results = "검색 모듈 미연결 (건너뜀)"
    
    # ----------------------------------------
    # Phase 2: Strategy Setup
    # ----------------------------------------
    add_log("🧠 Phase 2: AI 주무관이 업무 처리 방향을 수립합니다...", "strat")
    
    # [수정] Agents -> LegalAgents
    strategy = LegalAgents.strategist(user_input, legal_basis, search_results)

    # ----------------------------------------
    # Phase 3: Execution (Drafting)
    # ----------------------------------------
    add_log("📅 Phase 3: 기한 산정 및 공문서 작성 시작...", "calc")
    
    # [수정] legal_basis 인자 추가 (누락된 인자 보완)
    meta_info = LegalAgents.clerk(user_input, legal_basis)
    
    add_log("✍️ 최종 공문서 조판 중...", "draft")
    
    # [수정] strategy 인자 추가 (누락된 인자 보완)
    doc_data = LegalAgents.drafter(user_input, legal_basis, meta_info, strategy)
    
    # ----------------------------------------
    # Phase 4: Persistence (Saving)
    # ----------------------------------------
    add_log("💾 업무 기록을 데이터베이스(Supabase)에 저장 중...", "sys")
    
    # [수정] db -> db_service, save_report -> save_log (이름 통일)
    save_result = db_service.save_log(user_input, legal_basis, strategy, doc_data)
    
    add_log(f"✅ 모든 행정 절차가 완료되었습니다. ({save_result})", "sys")
    time.sleep(1) 
    
    # 로그창 지우기 (결과는 리턴값으로 나감)
    log_placeholder.empty()

    return {
        "doc": doc_data,
        "meta": meta_info,
        "law": legal_basis,
        "search": search_results,
        "strategy": strategy,
        "save_msg": save_result
    }

# ==========================================
# 5. Presentation Layer (UI)
# ==========================================
# [수정] main 함수 전체 교체
def main():
    col_left, col_right = st.columns([1, 1.2])

    # ---------------------------------------------------------
    # [왼쪽] 입력 및 결과 (새로고침 해도 안 사라짐)
    # ---------------------------------------------------------
    with col_left:
        st.title("🏢 AI 행정관 Pro")
        st.markdown("---")
        
        user_input = st.text_area("업무 지시", height=150, placeholder="예: 무단투기 과태료 부과 통지서 작성")
        
        # 1. 실행 버튼 (누르면 세션에 저장)
        if st.button("⚡ 스마트 행정 처분 시작", type="primary", use_container_width=True):
            if user_input:
                try:
                    with st.spinner("AI 에이전트가 분석 중입니다..."):
                        # [핵심] 결과를 세션에 '박제'
                        st.session_state['workflow_result'] = run_workflow(user_input)
                except Exception as e: st.error(f"오류: {e}")

        # 2. 결과 표시 (세션에 데이터가 있으면 무조건 그림)
        if 'workflow_result' in st.session_state:
            res = st.session_state['workflow_result']
            
            st.markdown("---")
            if "성공" in res.get('save_msg', ''): st.success(f"✅ {res['save_msg']}")
            else: st.error(f"❌ {res.get('save_msg')}")

            with st.expander("✅ [검토] 법령 및 근거 상세", expanded=True):
                st.code(res.get('law', ''), language="text")
                st.info(f"🔎 판례: {res.get('search', '')}")

            with st.expander("🧭 [방향] 처리 가이드라인", expanded=True):
                st.markdown(res.get('strategy', ''))

    # ---------------------------------------------------------
    # [오른쪽] 공문서 미리보기 (화면 깨짐 완벽 해결)
    # ---------------------------------------------------------
    with col_right:
        if 'workflow_result' in st.session_state:
            res = st.session_state['workflow_result']
            doc = res.get('doc')
            meta = res.get('meta')
            
            if doc:
                # 문단 HTML 변환
                paragraphs = doc.get('body_paragraphs', [])
                if isinstance(paragraphs, str): paragraphs = [paragraphs]
                p_html = "".join([f"<p style='margin-bottom: 15px;'>{p}</p>" for p in paragraphs])

                # [🚨 중요] HTML 코드는 들여쓰기 절대 금지! 왼쪽 벽에 딱 붙이세요.
                # 그래야 브라우저가 '코드'가 아니라 '디자인'으로 인식합니다.
                html_content = f"""
<div class="paper-sheet">
<div class="stamp">직인생략</div>
<div class="doc-header">{doc.get('title', '공 문 서')}</div>
<div class="doc-info">
<span>문서번호: {meta.get('doc_num', '')}</span>
<span>시행일자: {meta.get('today_str', '')}</span>
<span>수신: {doc.get('receiver', '참조')}</span>
</div>
<hr style="border: 1px solid black; margin-bottom: 30px;">
<div class="doc-body">
{p_html}
</div>
<div class="doc-footer">{doc.get('department_head', '행정기관장')}</div>
</div>
"""
                st.markdown(html_content, unsafe_allow_html=True)
                
                # 다운로드 버튼
                st.download_button(
                    label="🖨️ 다운로드 (HTML)",
                    data=html_content,
                    file_name="공문서.html",
                    mime="text/html",
                    use_container_width=True
                )
        else:
            # 대기 화면 HTML (이것도 왼쪽 벽에 붙임)
            st.markdown("""
<div style='text-align: center; padding: 100px; color: #aaa; background: white; border-radius: 10px; border: 2px dashed #ddd;'>
<h3>📄 Document Preview</h3>
<p>왼쪽에서 업무를 지시하면<br>완성된 공문서가 여기에 나타납니다.</p>
</div>
""", unsafe_allow_html=True)

if __name__ == "__main__":
    main()

import streamlit as st
import google.generativeai as genai
from groq import Groq
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
    .doc-body { font-size: 12pt; text-align: justify; }
    .doc-footer { text-align: center; font-size: 20pt; font-weight: bold; margin-top: 80px; letter-spacing: 5px; }
    .stamp { position: absolute; bottom: 85px; right: 80px; border: 3px solid #cc0000; color: #cc0000; padding: 5px 10px; font-size: 14pt; font-weight: bold; transform: rotate(-15deg); opacity: 0.8; border-radius: 5px; }
    
    /* 로그 스타일 */
    .agent-log { font-family: 'Consolas', monospace; font-size: 0.85rem; padding: 6px 12px; border-radius: 6px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
    .log-legal { background-color: #eff6ff; color: #1e40af; border-left: 4px solid #3b82f6; } /* Blue */
    .log-calc { background-color: #f0fdf4; color: #166534; border-left: 4px solid #22c55e; } /* Green */
    .log-draft { background-color: #fef2f2; color: #991b1b; border-left: 4px solid #ef4444; } /* Red */
    .log-sys { background-color: #f3f4f6; color: #4b5563; border-left: 4px solid #9ca3af; } /* Gray */
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Service Layer (Infrastructure)
# ==========================================
class LLMService:
    """Gemini 2.5 모델들을 순차적으로 시도하고, 실패 시 Groq로 백업하는 서비스"""
    def __init__(self):
        self.gemini_key = st.secrets["general"].get("GEMINI_API_KEY")
        self.groq_key = st.secrets["general"].get("GROQ_API_KEY")
        
        # [설정 변경] 요청하신 모델 우선순위 리스트
        # 1순위: gemini-2.5-flash-lite, 2순위: gemini-2.5-flash
        self.gemini_models = [
            "gemini-2.5-flash-lite", 
            "gemini-2.5-flash"
        ]
        
        if self.gemini_key:
            genai.configure(api_key=self.gemini_key)
            
        self.groq_client = Groq(api_key=self.groq_key) if self.groq_key else None

    def _try_gemini(self, prompt, is_json=False, schema=None):
        """지정된 Gemini 모델 리스트를 순회하며 생성을 시도"""
        for model_name in self.gemini_models:
            try:
                model = genai.GenerativeModel(model_name)
                
                # 설정: JSON 모드 여부에 따라 config 분기
                config = genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=schema
                ) if is_json else None
                
                # 생성 요청
                res = model.generate_content(prompt, generation_config=config)
                return res.text, model_name # 성공 시 결과와 모델명 반환
                
            except Exception as e:
                # 현재 모델 실패 시 다음 모델 시도 (로그는 내부적으로만 남김)
                continue
                
        raise Exception("All Gemini models failed")

    def generate_text(self, prompt):
        """텍스트 생성 (Gemini 2.5 Loop -> Groq Fallback)"""
        try:
            text, model_used = self._try_gemini(prompt, is_json=False)
            return text
        except Exception as gemini_error:
            # Gemini 모두 실패 시 Groq 시도
            if self.groq_client:
                return self._generate_groq(prompt)
            return f"Error: {gemini_error}"

    def generate_json(self, prompt, schema=None):
        """JSON 생성 (Gemini 2.5 Loop Only)"""
        try:
            # Gemini Native JSON Mode 시도
            text, model_used = self._try_gemini(prompt, is_json=True, schema=schema)
            return json.loads(text)
        except Exception:
            # Fallback: 텍스트로 받고 파싱 (Groq 등 활용 가능성 열어둠)
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

# 싱글톤 인스턴스
llm_service = LLMService()

# ==========================================
# 3. Agent Layer (Business Logic)
# ==========================================
class LegalAgents:
    """각 역할을 수행하는 에이전트 집합"""
    
    @staticmethod
    def researcher(situation):
        """법률 근거 탐색"""
        prompt = f"""
        당신은 30년 경력의 법제관입니다.
        상황: "{situation}"
        위 상황에 적용할 가장 정확한 '법령명'과 '관련 조항'을 하나만 찾으시오.
        반드시 현행 대한민국 법령이어야 하며, 조항 번호까지 명시하세요.
        (예: 도로교통법 제32조(정차 및 주차의 금지))
        """
        return llm_service.generate_text(prompt).strip()

    @staticmethod
    def clerk(situation, legal_basis):
        """날짜 및 기한 동적 산정"""
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
    def drafter(situation, legal_basis, meta_info):
        """공문서 작성"""
        doc_schema = {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "공문서 제목"},
                "receiver": {"type": "STRING", "description": "수신인"},
                "body_paragraphs": {
                    "type": "ARRAY", 
                    "items": {"type": "STRING"},
                    "description": "본문 단락 리스트"
                },
                "department_head": {"type": "STRING", "description": "발신 명의 (예: OO시장)"}
            },
            "required": ["title", "receiver", "body_paragraphs", "department_head"]
        }

        prompt = f"""
        당신은 행정기관의 베테랑 서기입니다. 아래 정보를 바탕으로 완결된 공문서를 작성하세요.
        
        [입력 정보]
        - 민원 상황: {situation}
        - 법적 근거: {legal_basis}
        - 문서 번호: {meta_info['doc_num']}
        - 시행 일자: {meta_info['today_str']}
        - 제출 기한: {meta_info['deadline_str']} ({meta_info['days_added']}일 부여됨)
        
        [작성 원칙]
        1. 수신인이 불명확하면 상황에 맞춰 'OOO 귀하', '차량소유주 귀하' 등으로 추론.
        2. 본문은 [처분 원인 및 경과] -> [법적 근거] -> [처분 내용 및 기한] -> [불이행 시 조치/구제절차] 순서로 작성.
        3. 어조는 정중하되 단호한 공문서 표준어 사용.
        """
        
        return llm_service.generate_json(prompt, schema=doc_schema)

# ==========================================
# 4. Use Case (Orchestration)
# ==========================================
def run_workflow(user_input):
    """에이전트 조율 및 실행"""
    log_placeholder = st.empty()
    logs = []

    def add_log(msg, style="sys"):
        logs.append(f"<div class='agent-log log-{style}'>{msg}</div>")
        log_placeholder.markdown("".join(logs), unsafe_allow_html=True)
        time.sleep(0.5)

    # 1. 법률 분석
    add_log("👨‍⚖️ Legal Agent: 법령 및 판례 데이터베이스 검색 중...", "legal")
    legal_basis = LegalAgents.researcher(user_input)
    add_log(f"📜 법적 근거 확보: {legal_basis}", "legal")

    # 2. 행정 처리
    add_log("📅 Clerk Agent: 행정절차법에 따른 기한 산정 중...", "calc")
    meta_info = LegalAgents.clerk(user_input, legal_basis)
    add_log(f"⏳ 기한 설정: {meta_info['days_added']}일 ({meta_info['deadline_str']} 까지)", "calc")

    # 3. 문서 작성
    add_log("✍️ Drafter Agent: 공문서 표준 서식 적용 및 조판 중...", "draft")
    doc_data = LegalAgents.drafter(user_input, legal_basis, meta_info)
    
    add_log("✅ 모든 행정 절차가 완료되었습니다.", "sys")
    time.sleep(1)
    log_placeholder.empty()

    return doc_data, meta_info

# ==========================================
# 5. UI Presentation (Main App)
# ==========================================
def main():
    col_left, col_right = st.columns([1, 1.2])

    with col_left:
        st.title("🏢 AI 행정관")
        st.caption("Gemini 2.5 Powered Action Agent")
        st.markdown("---")
        
        st.markdown("### 🗣️ 업무 지시")
        st.markdown("상황을 구체적으로 입력하세요. AI가 법령 검토부터 문서 작성까지 일괄 처리합니다.")
        
        user_input = st.text_area(
            "업무 내용",
            height=150,
            placeholder="예시:\n- 아파트 단지 내 소방차 전용구역 불법 주차 차량 과태료 부과 예고 통지서 작성해줘.\n- 식품위생법 위반 식당 영업정지 사전 통지서 써줘.",
            label_visibility="collapsed"
        )
        
        if st.button("⚡ 행정 처분 시작", type="primary", use_container_width=True):
            if not user_input:
                st.warning("내용을 입력해주세요.")
            else:
                try:
                    with st.spinner("Gemini 2.5 에이전트들이 협업 중입니다..."):
                        doc, meta = run_workflow(user_input)
                        st.session_state['final_doc'] = (doc, meta)
                except Exception as e:
                    st.error(f"시스템 오류 발생: {e}")

        st.markdown("---")
        st.info("💡 **Tip:** 복잡한 양식을 고민하지 마세요. '누가, 무엇을, 왜'만 입력하면 됩니다.")

    with col_right:
        if 'final_doc' in st.session_state:
            doc, meta = st.session_state['final_doc']
            
            if doc:
                # A4 용지 렌더링 (HTML/CSS)
                html_content = f"""
                <div class="paper-sheet">
                    <div class="stamp">직인생략</div>
                    <div class="doc-header">{doc.get('title', '공 문 서')}</div>
                    <div class="doc-info">
                        <span>문서번호: {meta['doc_num']}</span>
                        <span>시행일자: {meta['today_str']}</span>
                        <span>수신: {doc.get('receiver', '수신자 참조')}</span>
                    </div>
                    <hr style="border: 1px solid black; margin-bottom: 30px;">
                    <div class="doc-body">
                """
                
                paragraphs = doc.get('body_paragraphs', [])
                if isinstance(paragraphs, str): paragraphs = [paragraphs]
                
                for p in paragraphs:
                    html_content += f"<p style='margin-bottom: 15px;'>{p}</p>"
                
                html_content += f"""
                    </div>
                    <div class="doc-footer">
                        {doc.get('department_head', '행정기관장')}
                    </div>
                </div>
                """
                
                st.markdown(html_content, unsafe_allow_html=True)
                
                st.download_button(
                    label="🖨️ 다운로드 (HTML)",
                    data=html_content,
                    file_name="공문서.html",
                    mime="text/html",
                    use_container_width=True
                )
        else:
            st.markdown("""
            <div style='text-align: center; padding: 100px; color: #aaa; background: white; border-radius: 10px; border: 2px dashed #ddd;'>
                <h3>📄 Document Preview</h3>
                <p>왼쪽에서 업무를 지시하면<br>완성된 공문서가 여기에 나타납니다.</p>
            </div>
            """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()

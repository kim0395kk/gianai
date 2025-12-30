import streamlit as st
import google.generativeai as genai
from groq import Groq
from serpapi import GoogleSearch
from supabase import create_client
import requests
import xml.etree.ElementTree as ET
import json
import re
import time
from datetime import datetime, timedelta

# ==========================================
# 1. Configuration & Styles
# ==========================================
st.set_page_config(layout="wide", page_title="AI Bureau: The Legal Glass", page_icon="⚖️")

st.markdown("""
<style>
    .stApp { background-color: #f3f4f6; }
    .paper-sheet {
        background-color: white; width: 100%; max-width: 210mm; min-height: 297mm;
        padding: 25mm; margin: auto; box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        font-family: 'Batang', serif; color: #111; line-height: 1.6; position: relative;
    }
    .doc-header { text-align: center; font-size: 22pt; font-weight: 900; margin-bottom: 30px; letter-spacing: 2px; }
    .doc-info { display: flex; justify-content: space-between; font-size: 11pt; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 20px; }
    .doc-body { font-size: 12pt; text-align: justify; }
    .doc-footer { text-align: center; font-size: 20pt; font-weight: bold; margin-top: 80px; letter-spacing: 5px; }
    .stamp { position: absolute; bottom: 85px; right: 80px; border: 3px solid #cc0000; color: #cc0000; padding: 5px 10px; font-size: 14pt; font-weight: bold; transform: rotate(-15deg); opacity: 0.8; border-radius: 5px; }
    
    .agent-log { font-family: 'Consolas', monospace; font-size: 0.85rem; padding: 6px 12px; border-radius: 6px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
    .log-legal { background-color: #eff6ff; color: #1e40af; border-left: 4px solid #3b82f6; }
    .log-search { background-color: #fff7ed; color: #c2410c; border-left: 4px solid #f97316; }
    .log-strat { background-color: #f5f3ff; color: #6d28d9; border-left: 4px solid #8b5cf6; }
    .log-calc { background-color: #f0fdf4; color: #166534; border-left: 4px solid #22c55e; }
    .log-draft { background-color: #fef2f2; color: #991b1b; border-left: 4px solid #ef4444; }
    .log-sys { background-color: #f3f4f6; color: #4b5563; border-left: 4px solid #9ca3af; }
    
    .strategy-box { 
        background-color: #fffbeb; border: 2px solid #fcd34d; padding: 20px; 
        border-radius: 10px; margin-bottom: 20px; color: #451a03; 
        font-size: 1.05rem; line-height: 1.6; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    .strategy-title { font-weight: bold; color: #b45309; margin-bottom: 10px; font-size: 1.2rem; border-bottom: 1px dashed #b45309; padding-bottom: 5px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Services (Infrastructure)
# ==========================================

class LLMService:
    def __init__(self):
        self.gemini_key = st.secrets["general"].get("GEMINI_API_KEY")
        self.groq_key = st.secrets["general"].get("GROQ_API_KEY")
        self.gemini_models = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
        if self.gemini_key: genai.configure(api_key=self.gemini_key)
        self.groq_client = Groq(api_key=self.groq_key) if self.groq_key else None

    def _try_gemini(self, prompt, is_json=False, schema=None):
        for model_name in self.gemini_models:
            try:
                model = genai.GenerativeModel(model_name)
                config = genai.GenerationConfig(response_mime_type="application/json", response_schema=schema) if is_json else None
                res = model.generate_content(prompt, generation_config=config)
                return res.text
            except: continue
        return None

    def generate_text(self, prompt):
        text = self._try_gemini(prompt, is_json=False)
        if text: return text
        return self._generate_groq(prompt) if self.groq_client else "AI 모델 응답 없음"

    def generate_json(self, prompt, schema=None):
        """[핵심 수정] JSON 파싱 안전장치 강화"""
        text = self._try_gemini(prompt, is_json=True, schema=schema)
        
        # Gemini 실패 시 일반 텍스트 모드로 재시도 (Groq 백업 등)
        if not text:
            text = self.generate_text(prompt + "\n\nOutput strictly in JSON format.")
        
        if not text: return None

        try:
            # 1. 마크다운 코드 블록 제거 (```json ... ```)
            clean_text = re.sub(r"```json|```", "", text).strip()
            
            # 2. 중괄호 {} 사이 내용만 추출 (앞뒤 사족 제거)
            match = re.search(r'\{.*\}', clean_text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            else:
                # 3. 매칭 실패 시 전체 파싱 시도
                return json.loads(clean_text)
        except Exception as e:
            print(f"JSON Parsing Failed: {e} \nText: {text}")
            return None

    def _generate_groq(self, prompt):
        try:
            completion = self.groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            return completion.choices[0].message.content
        except: return None

class NationalLawService:
    def __init__(self):
        self.api_id = st.secrets["general"].get("LAW_API_ID")
        self.base_url = "[https://www.law.go.kr/DRF/lawSearch.do](https://www.law.go.kr/DRF/lawSearch.do)"
        self.detail_url = "[https://www.law.go.kr/DRF/lawService.do](https://www.law.go.kr/DRF/lawService.do)"

    def get_specific_article(self, law_name, article_num):
        if not self.api_id: return "(법령 API ID 없음)"

        try:
            # 1. 법령 ID 찾기
            params = {"OC": self.api_id, "target": "law", "type": "XML", "query": law_name, "display": 1}
            res = requests.get(self.base_url, params=params, timeout=5)
            root = ET.fromstring(res.content)
            law_node = root.find(".//law")
            
            if law_node is None: return f"'{law_name}' 검색 실패. (일반 조항 적용)"
            
            law_id = law_node.find("법령일련번호").text
            official_name = law_node.find("법령명한글").text
            
            # 2. 상세 본문 가져오기
            d_params = {"OC": self.api_id, "target": "law", "type": "XML", "MST": law_id}
            d_res = requests.get(self.detail_url, params=d_params, timeout=10)
            d_root = ET.fromstring(d_res.content)
            
            # 3. 조 번호 필터링
            target_num = re.sub(r'[^0-9]', '', str(article_num))
            found_articles = []
            for article in d_root.findall(".//조문"):
                xml_num = article.find("조문번호").text
                if xml_num == target_num:
                    content = article.find("조문내용").text or ""
                    sub_texts = []
                    for sub in article.findall(".//항"):
                        sub_content = sub.find("항내용").text or ""
                        if sub_content: sub_texts.append(f"  - {sub_content}")
                    
                    full_text = f"[{official_name} 제{xml_num}조] {content}"
                    if sub_texts: full_text += "\n" + "\n".join(sub_texts)
                    found_articles.append(full_text)
                    
            if found_articles:
                return "\n\n".join(found_articles)
            else:
                return f"'{official_name}' 제{target_num}조 원문 조회 실패."
                
        except Exception as e:
            return f"법령 API 오류: {e}"

class SearchService:
    def __init__(self): self.api_key = st.secrets["general"].get("SERPAPI_KEY")
    def search_google(self, query):
        if not self.api_key: return "API 키 없음 (건너뜀)"
        try:
            params = {"engine": "google", "q": query + " 행정처분 판례", "api_key": self.api_key, "num": 3, "hl": "ko", "gl": "kr"}
            search = GoogleSearch(params)
            results = search.get_dict().get("organic_results", [])
            return "\n".join([f"- [{item['title']}]({item['link']}): {item['snippet']}" for item in results]) if results else "관련 결과 없음"
        except: return "검색 서비스 오류"

class DatabaseService:
    def __init__(self):
        try:
            self.url = st.secrets["supabase"]["SUPABASE_URL"]
            self.key = st.secrets["supabase"]["SUPABASE_KEY"]
            self.client = create_client(self.url, self.key)
            self.is_active = True
        except: self.is_active = False
    
    def save_report(self, user_input, legal_basis, doc_data):
        if not self.is_active: return "DB 설정 없음"
        try:
            summary_text = json.dumps(doc_data, ensure_ascii=False, indent=2)
            # vector는 선택사항이므로 일단 텍스트만 저장
            data = {"situation": user_input, "law_name": legal_basis, "summary": summary_text}
            self.client.table("law_reports").insert(data).execute()
            return "저장 성공"
        except Exception as e: return f"저장 실패({e})"

# 인스턴스 생성
llm_service = LLMService()
law_api = NationalLawService()
search_service = SearchService()
db_service = DatabaseService()

# ==========================================
# 3. Domain Layer (Agents)
# ==========================================
class LegalAgents:
    @staticmethod
    def researcher(situation):
        # Step 1. LLM 추론 (파싱 실패 시 기본값 사용)
        guess_prompt = f"""
        상황: "{situation}"
        이 상황에 적용될 가장 유력한 대한민국 법령명과 조항 번호(숫자만)를 추론하여 JSON으로 출력하시오.
        Format: {{ "law": "도로교통법", "article_num": 32 }}
        """
        law_name = "민원 처리에 관한 법률"
        article_num = 1
        
        guess = llm_service.generate_json(guess_prompt)
        if guess and isinstance(guess, dict):
            law_name = guess.get("law", law_name)
            article_num = guess.get("article_num", article_num)
            
        # Step 2. API 검증
        return law_api.get_specific_article(law_name, article_num)

    @staticmethod
    def strategist(situation, legal_basis, search_results):
        prompt = f"""
        [상황]: {situation}
        [검증된 법적 근거]: {legal_basis}
        [유사 사례]: {search_results}
        
        위 정보를 종합하여 **업무 처리 전략(Strategy)**을 수립하세요. (마크다운)
        1. **처리 방향**: (강경/계도/반려 등)
        2. **핵심 주의사항**: (절차적 흠결 방지)
        3. **대응 논리**: (민원인 반발 시)
        """
        return llm_service.generate_text(prompt)

    @staticmethod
    def clerk(situation, legal_basis):
        today = datetime.now()
        prompt = f"오늘: {today.strftime('%Y-%m-%d')}, 법령: {legal_basis}. 법적 의견제출 기한(일수) 숫자만 출력. (기본 15)"
        try:
            res = llm_service.generate_text(prompt)
            days = int(re.sub(r'[^0-9]', '', res))
        except: days = 15
        deadline = today + timedelta(days=days)
        return {
            "today_str": today.strftime("%Y. %m. %d."),
            "deadline_str": deadline.strftime("%Y. %m. %d."),
            "days_added": days,
            "doc_num": f"행정-{today.strftime('%Y')}-{int(time.time())%1000:03d}호"
        }

    @staticmethod
    def drafter(situation, legal_basis, meta_info, strategy):
        doc_schema = {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"}, "receiver": {"type": "STRING"},
                "body_paragraphs": {"type": "ARRAY", "items": {"type": "STRING"}},
                "department_head": {"type": "STRING"}
            },
            "required": ["title", "receiver", "body_paragraphs", "department_head"]
        }
        prompt = f"""
        베테랑 서기입니다. 공문서를 작성하세요.
        상황: {situation}, 검증된 근거: {legal_basis}, 기한: {meta_info['deadline_str']}
        전략: {strategy}
        작성원칙: 정중하고 단호하게. 개인정보 마스킹.
        """
        
        result = llm_service.generate_json(prompt, schema=doc_schema)
        
        # [방어 로직] 문서 생성 실패 시 비상용 포맷 반환
        if not result:
            return {
                "title": "안 내 문 (자동생성 실패)", 
                "receiver": "민원인 귀하", 
                "body_paragraphs": [
                    "시스템 오류로 인해 문서 내용을 생성하지 못했습니다.",
                    "입력하신 내용을 바탕으로 다시 시도해주시거나, 관리자에게 문의 바랍니다."
                ], 
                "department_head": "시스템 관리자"
            }
        return result

# ==========================================
# 4. Workflow
# ==========================================
def run_workflow(user_input):
    log_placeholder = st.empty()
    logs = []
    def add_log(msg, style="sys"):
        logs.append(f"<div class='agent-log log-{style}'>{msg}</div>")
        log_placeholder.markdown("".join(logs), unsafe_allow_html=True)
        time.sleep(0.3)

    # 1. 리서치
    add_log("🤔 Phase 1: AI가 법령 추론 및 API 검증 중...", "legal")
    legal_basis = LegalAgents.researcher(user_input)
    
    add_log("🌍 Phase 1-2: 판례 및 사례 검색 중...", "search")
    search_results = search_service.search_google(user_input)
    
    with st.expander("✅ [팩트체크] 검증된 법령 및 사례", expanded=True):
        col1, col2 = st.columns(2)
        with col1: st.info(f"**API 검증 결과**\n\n{legal_basis}")
        with col2: st.warning(f"**판례/사례**\n\n{search_results}")

    # 2. 전략 수립
    add_log("🧠 Phase 2: 업무 처리 전략 수립...", "strat")
    strategy = LegalAgents.strategist(user_input, legal_basis, search_results)
    
    st.markdown(f"""<div class="strategy-box"><div class="strategy-title">🧭 AI 주무관의 업무 가이드라인</div>{strategy}</div>""", unsafe_allow_html=True)

    # 3. 문서 작성
    add_log("✍️ Phase 3: 공문서 작성 및 기한 산정...", "draft")
    meta_info = LegalAgents.clerk(user_input, legal_basis)
    doc_data = LegalAgents.drafter(user_input, legal_basis, meta_info, strategy)
    
    # 4. 저장
    add_log("💾 DB 저장 중...", "sys")
    save_msg = db_service.save_report(user_input, legal_basis, doc_data)
    
    add_log(f"✅ 완료 ({save_msg})", "sys")
    time.sleep(1)
    log_placeholder.empty()

    return doc_data, meta_info

# ==========================================
# 5. Main UI
# ==========================================
def main():
    col_left, col_right = st.columns([1, 1.2])

    with col_left:
        st.title("🏢 AI 행정관 Pro")
        st.caption("Token Optimized & Fail-Safe Architecture")
        st.markdown("---")
        
        user_input = st.text_area("업무 내용", height=150, placeholder="예: 무단적치물 계고장 작성해줘")
        
        if st.button("⚡ 실행", type="primary", use_container_width=True):
            if not user_input:
                st.warning("내용을 입력하세요.")
            else:
                try:
                    with st.spinner("AI 에이전트들이 작업 중입니다..."):
                        doc, meta = run_workflow(user_input)
                        st.session_state['final_doc'] = (doc, meta)
                except Exception as e:
                    st.error(f"치명적 오류 발생: {e}")

    with col_right:
        if 'final_doc' in st.session_state:
            doc, meta = st.session_state['final_doc']
            if doc:
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
                    <div class="doc-footer">{doc.get('department_head', '행정기관장')}</div>
                </div>
                """
                st.markdown(html_content, unsafe_allow_html=True)
                st.download_button(label="🖨️ 다운로드 (HTML)", data=html_content, file_name="공문서.html", mime="text/html", use_container_width=True)
        else:
            st.markdown("<div style='text-align:center;padding:100px;color:#aaa;'><h3>📄 문서 미리보기</h3><p>좌측에서 내용을 입력하고 실행하세요.</p></div>", unsafe_allow_html=True)

if __name__ == "__main__":
    main()

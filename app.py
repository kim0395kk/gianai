import streamlit as st
import google.generativeai as genai
from groq import Groq
from supabase import create_client
import json
import re
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from html import escape as _escape

# ==========================================
# 0) Settings
# ==========================================
MAX_FOLLOWUP_Q = 5  # ✅ 후속 질문 최대 5회

# ==========================================
# 1) Configuration & Styles
# ==========================================
st.set_page_config(layout="wide", page_title="AI Bureau: The Legal Glass", page_icon="⚖️")

st.markdown(
    """
<style>
    .stApp { background-color: #f3f4f6; }

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

    .doc-header { text-align: center; font-size: 22pt; font-weight: 900; margin-bottom: 30px; letter-spacing: 2px; }
    .doc-info { display: flex; justify-content: space-between; font-size: 11pt; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 20px; gap:10px; flex-wrap:wrap; }
    .doc-body { font-size: 12pt; text-align: justify; white-space: pre-line; }
    .doc-footer { text-align: center; font-size: 20pt; font-weight: bold; margin-top: 80px; letter-spacing: 5px; }
    .stamp { position: absolute; bottom: 85px; right: 80px; border: 3px solid #cc0000; color: #cc0000; padding: 5px 10px; font-size: 14pt; font-weight: bold; transform: rotate(-15deg); opacity: 0.8; border-radius: 5px; }

    .agent-log { font-family: 'Consolas', monospace; font-size: 0.85rem; padding: 6px 12px; border-radius: 6px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
    .log-legal { background-color: #eff6ff; color: #1e40af; border-left: 4px solid #3b82f6; }
    .log-search { background-color: #fff7ed; color: #c2410c; border-left: 4px solid #f97316; }
    .log-strat { background-color: #f5f3ff; color: #6d28d9; border-left: 4px solid #8b5cf6; }
    .log-calc { background-color: #f0fdf4; color: #166534; border-left: 4px solid #22c55e; }
    .log-draft { background-color: #fef2f2; color: #991b1b; border-left: 4px solid #ef4444; }
    .log-sys { background-color: #f3f4f6; color: #4b5563; border-left: 4px solid #9ca3af; }

    /* Streamlit Cloud 상단 Fork/GitHub 숨김 */
    header [data-testid="stToolbar"] { display: none !important; }
    header [data-testid="stDecoration"] { display: none !important; }
    header { height: 0px !important; }
    footer { display: none !important; }
    div[data-testid="stStatusWidget"] { display: none !important; }
</style>
""",
    unsafe_allow_html=True,
)

# ==========================================
# 2) Infrastructure Services
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
        g = st.secrets.get("general", {})
        self.gemini_key = g.get("GEMINI_API_KEY")
        self.groq_key = g.get("GROQ_API_KEY")

        self.gemini_models = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
        ]

        if self.gemini_key:
            genai.configure(api_key=self.gemini_key)

        self.groq_client = Groq(api_key=self.groq_key) if self.groq_key else None

    def _try_gemini(self, prompt, is_json=False, schema=None):
        for model_name in self.gemini_models:
            try:
                model = genai.GenerativeModel(model_name)
                config = genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                ) if is_json else None
                res = model.generate_content(prompt, generation_config=config)
                return res.text, model_name
            except Exception:
                continue
        raise Exception("All Gemini models failed")

    def generate_text(self, prompt):
        try:
            text, _ = self._try_gemini(prompt, is_json=False)
            return text
        except Exception:
            if self.groq_client:
                return self._generate_groq(prompt)
            return "시스템 오류: AI 모델 연결 실패"

    def generate_json(self, prompt, schema=None):
        try:
            text, _ = self._try_gemini(prompt, is_json=True, schema=schema)
            return json.loads(text)
        except Exception:
            text = self.generate_text(prompt + "\n\nOutput strictly in JSON.")
            try:
                match = re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)
                return json.loads(match.group(0)) if match else None
            except Exception:
                return None

    def _generate_groq(self, prompt):
        try:
            completion = self.groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            return completion.choices[0].message.content
        except Exception:
            return "System Error"


class SearchService:
    """✅ 뉴스 중심 경량 검색"""
    def __init__(self):
        g = st.secrets.get("general", {})
        self.client_id = g.get("NAVER_CLIENT_ID")
        self.client_secret = g.get("NAVER_CLIENT_SECRET")
        self.news_url = "https://openapi.naver.com/v1/search/news.json"

    def _headers(self):
        return {"X-Naver-Client-Id": self.client_id, "X-Naver-Client-Secret": self.client_secret}

    def _clean_html(self, s: str) -> str:
        if not s:
            return ""
        s = re.sub(r"<[^>]+>", "", s)
        s = re.sub(r"&quot;", '"', s)
        s = re.sub(r"&lt;", "<", s)
        s = re.sub(r"&gt;", ">", s)
        s = re.sub(r"&amp;", "&", s)
        return s.strip()

    def _extract_keywords_llm(self, situation: str) -> str:
        prompt = f"상황: '{situation}'\n뉴스 검색을 위한 핵심 키워드 2개만 콤마로 구분해 출력."
        try:
            res = llm_service.generate_text(prompt).strip()
            return re.sub(r'[".?]', "", res)
        except Exception:
            return situation[:20]

    def search_news(self, query: str, top_k: int = 3) -> str:
        if not self.client_id or not self.client_secret:
            return "⚠️ 네이버 API 키가 없습니다."
        if not query:
            return "⚠️ 검색어가 비었습니다."

        try:
            params = {"query": query, "display": 10, "sort": "sim"}
            res = requests.get(self.news_url, headers=self._headers(), params=params, timeout=8)
            res.raise_for_status()
            items = res.json().get("items", [])

            if not items:
                return f"🔍 `{query}` 관련 최신 사례가 없습니다."

            lines = [f"📰 **최신 뉴스 사례 (검색어: {query})**", "---"]
            for it in items[:top_k]:
                title = self._clean_html(it.get("title", ""))
                desc = self._clean_html(it.get("description", ""))
                link = it.get("link", "#")
                lines.append(f"- **[{title}]({link})**\n  : {desc[:150]}...")
            return "\n".join(lines)
        except Exception as e:
            return f"검색 중 오류: {str(e)}"

    def search_precedents(self, situation: str, top_k: int = 3) -> str:
        keywords = self._extract_keywords_llm(situation)
        return self.search_news(keywords, top_k=top_k)


class DatabaseService:
    """
    ✅ 로그인 없이도 DB 저장 (요청사항)
    - 최초 workflow 결과 insert 후 report_id 확보
    - 후속 Q/A 및 추가 조회 내용은 summary JSON에 누적해 update
    """
    def __init__(self):
        try:
            self.url = st.secrets["supabase"]["SUPABASE_URL"]
            self.key = (
                st.secrets["supabase"].get("SUPABASE_KEY")
                or st.secrets["supabase"].get("SUPABASE_ANON_KEY")
            )
            self.client = create_client(self.url, self.key)
            self.is_active = True
        except Exception:
            self.is_active = False
            self.client = None

    def _pack_summary(self, res: dict, followup: dict) -> str:
        payload = {
            "meta": res.get("meta"),
            "strategy": res.get("strategy"),
            "search_initial": res.get("search"),
            "law_initial": res.get("law"),
            "document_content": res.get("doc"),
            "followup": followup,
        }
        return json.dumps(payload, ensure_ascii=False)

    def insert_initial_report(self, res: dict) -> dict:
        """초기 분석 결과 insert -> report_id 반환"""
        if not self.is_active:
            return {"ok": False, "msg": "DB 미연결 (저장 건너뜀)", "id": None}

        try:
            followup = {"count": 0, "messages": [], "extra_context": ""}
            data = {
                "situation": res.get("situation", ""),
                "law_name": res.get("law", ""),
                "summary": self._pack_summary(res, followup),
            }
            resp = self.client.table("law_reports").insert(data).execute()

            inserted_id = None
            try:
                if hasattr(resp, "data") and resp.data and isinstance(resp.data, list):
                    inserted_id = resp.data[0].get("id")
            except Exception:
                inserted_id = None

            return {"ok": True, "msg": "DB 저장 성공", "id": inserted_id}
        except Exception as e:
            return {"ok": False, "msg": f"DB 저장 실패: {e}", "id": None}

    def update_followup(self, report_id, res: dict, followup: dict) -> dict:
        """후속 Q/A 누적 저장(가능하면 update, 안 되면 insert fallback)"""
        if not self.is_active:
            return {"ok": False, "msg": "DB 미연결 (업데이트 건너뜀)"}

        summary = self._pack_summary(res, followup)

        # 1) update 시도
        if report_id is not None:
            try:
                self.client.table("law_reports").update({"summary": summary}).eq("id", report_id).execute()
                return {"ok": True, "msg": "DB 업데이트 성공"}
            except Exception:
                pass

        # 2) fallback: 새로 insert
        try:
            data = {
                "situation": res.get("situation", ""),
                "law_name": res.get("law", ""),
                "summary": summary,
            }
            self.client.table("law_reports").insert(data).execute()
            return {"ok": True, "msg": "DB 업데이트 실패 → 신규 저장(fallback) 완료"}
        except Exception as e:
            return {"ok": False, "msg": f"DB 업데이트/저장 실패: {e}"}


class LawOfficialService:
    """
    국가법령정보센터(law.go.kr) 공식 API 연동

    ✅ 후속질문에서 발생한 '링크는 줬는데 법령이 없다' 오류 원인:
    - lawService.do?ID=... 조합이 환경/값에 따라 불일치하는 경우가 있음(특히 000213 같은 값)
    - 해결: 검색 결과의 MST(법령일련번호)를 기반으로 링크를 생성(가장 안정적)
      => https://www.law.go.kr/DRF/lawService.do?OC=...&target=law&MST=<mst>&type=HTML
    - efYd(시행일) 파라미터는 넣지 않아서 "현행 아님" 문제를 최대한 회피
    """
    def __init__(self):
        self.api_id = st.secrets.get("general", {}).get("LAW_API_ID")
        self.base_url = "http://www.law.go.kr/DRF/lawSearch.do"
        self.service_url = "http://www.law.go.kr/DRF/lawService.do"

    def _make_current_link(self, mst_id: str) -> str | None:
        if not self.api_id or not mst_id:
            return None
        # ✅ efYd 파라미터 미포함(현행 아닙니다 이슈 회피)
        return f"https://www.law.go.kr/DRF/lawService.do?OC={self.api_id}&target=law&MST={mst_id}&type=HTML"

    def get_law_text(self, law_name, article_num=None, return_link: bool = False):
        if not self.api_id:
            msg = "⚠️ API ID(OC)가 설정되지 않았습니다."
            return (msg, None) if return_link else msg

        # 1) 법령 검색 -> MST 확보
        mst_id = ""
        try:
            params = {"OC": self.api_id, "target": "law", "type": "XML", "query": law_name, "display": 1}
            res = requests.get(self.base_url, params=params, timeout=6)
            root = ET.fromstring(res.content)

            law_node = root.find(".//law")
            if law_node is None:
                msg = f"🔍 '{law_name}'에 대한 검색 결과가 없습니다."
                return (msg, None) if return_link else msg

            mst_id = (law_node.findtext("법령일련번호") or "").strip()
        except Exception as e:
            msg = f"API 검색 중 오류: {e}"
            return (msg, None) if return_link else msg

        current_link = self._make_current_link(mst_id)

        # 2) 상세 조문 가져오기 (MST 기반)
        try:
            if not mst_id:
                msg = f"✅ '{law_name}'이(가) 확인되었습니다.\n(법령일련번호(MST) 추출 실패)\n🔗 현행 원문: {current_link or '-'}"
                return (msg, current_link) if return_link else msg

            detail_params = {"OC": self.api_id, "target": "law", "type": "XML", "MST": mst_id}
            res_detail = requests.get(self.service_url, params=detail_params, timeout=10)
            root_detail = ET.fromstring(res_detail.content)

            # 조문번호 지정된 경우: 해당 조문만
            if article_num:
                for article in root_detail.findall(".//조문단위"):
                    jo_num_tag = article.find("조문번호")
                    jo_content_tag = article.find("조문내용")
                    if jo_num_tag is None or jo_content_tag is None:
                        continue

                    current_num = (jo_num_tag.text or "").strip()
                    if str(article_num) == current_num:
                        target_text = f"[{law_name} 제{current_num}조 전문]\n" + _escape((jo_content_tag.text or "").strip())
                        for hang in article.findall(".//항"):
                            hang_content = hang.find("항내용")
                            if hang_content is not None:
                                target_text += f"\n  - {(hang_content.text or '').strip()}"
                        return (target_text, current_link) if return_link else target_text

            # 못 찾았거나 조문번호 미지정
            msg = f"✅ '{law_name}'이(가) 확인되었습니다.\n(상세 조문 자동 추출 실패 또는 조문번호 미지정)\n🔗 현행 원문: {current_link or '-'}"
            return (msg, current_link) if return_link else msg

        except Exception as e:
            msg = f"상세 법령 파싱 실패: {e}"
            return (msg, current_link) if return_link else msg


# ==========================================
# 3) Global Instances
# ==========================================
llm_service = LLMService()
search_service = SearchService()
db_service = DatabaseService()
law_api_service = LawOfficialService()


# ==========================================
# 4) Agents
# ==========================================
class LegalAgents:
    @staticmethod
    def researcher(situation):
        prompt_extract = f"""
상황: "{situation}"

위 민원 처리를 위해 법적 근거로 삼아야 할 핵심 대한민국 법령과 조문 번호를
**중요도 순으로 최대 3개까지** JSON 리스트로 추출하시오.

형식: [{{"law_name": "도로교통법", "article_num": 32}}, ...]
* 법령명은 정식 명칭 사용. 조문 번호 불명확하면 null.
"""
        search_targets = []
        try:
            extracted = llm_service.generate_json(prompt_extract)
            if isinstance(extracted, list):
                search_targets = extracted
            elif isinstance(extracted, dict):
                search_targets = [extracted]
        except Exception:
            search_targets = [{"law_name": "도로교통법", "article_num": None}]

        if not search_targets:
            search_targets = [{"law_name": "도로교통법", "article_num": None}]

        report_lines = []
        api_success_count = 0

        report_lines.append(f"🔍 **AI가 식별한 핵심 법령 ({len(search_targets)}건)**")
        report_lines.append("---")

        for idx, item in enumerate(search_targets):
            law_name = item.get("law_name", "관련법령")
            article_num = item.get("article_num")

            law_text, current_link = law_api_service.get_law_text(law_name, article_num, return_link=True)

            error_keywords = ["검색 결과가 없습니다", "오류", "API ID", "실패"]
            is_success = not any(k in (law_text or "") for k in error_keywords)

            if is_success:
                api_success_count += 1
                # ✅ 법령명 클릭 -> 새창에서 현행 원문
                law_title = f"[{law_name}]({current_link})" if current_link else law_name
                header = f"✅ **{idx+1}. {law_title} 제{article_num}조 (확인됨)**"
                content = law_text
            else:
                header = f"⚠️ **{idx+1}. {law_name} 제{article_num}조 (API 조회 실패)**"
                content = "(국가법령정보센터에서 해당 조문을 찾지 못했습니다. 법령명이 정확한지 확인이 필요합니다.)"

            report_lines.append(f"{header}\n{content}\n")

        final_report = "\n".join(report_lines)

        if api_success_count == 0:
            prompt_fallback = f"""
Role: 행정 법률 전문가
Task: 아래 상황에 적용될 법령과 조항을 찾아 설명하시오.
상황: "{situation}"

* 경고: 현재 외부 법령 API 연결이 원활하지 않습니다.
반드시 상단에 [AI 추론 결과]임을 명시하고 환각 가능성을 경고하시오.
"""
            ai_fallback_text = llm_service.generate_text(prompt_fallback).strip()

            return f"""⚠️ **[시스템 경고: API 조회 실패]**
(국가법령정보센터 연결 실패로 AI 지식 기반 답변입니다. **환각 가능성** 있으니 법제처 확인 필수)

--------------------------------------------------
{ai_fallback_text}"""

        return final_report

    @staticmethod
    def strategist(situation, legal_basis, search_results):
        prompt = f"""
당신은 행정 업무 베테랑 '주무관'입니다.

[민원 상황]: {situation}
[확보된 법적 근거]:
{legal_basis}

[유사 사례/판례]: {search_results}

위 정보를 종합하여 민원 처리 방향(Strategy)을 수립하세요.
서론(인사말/공감/네 알겠습니다 등) 금지.

1. 처리 방향
2. 핵심 주의사항
3. 예상 반발 및 대응
"""
        return llm_service.generate_text(prompt)

    @staticmethod
    def clerk(situation, legal_basis):
        today = datetime.now()
        prompt = f"""
오늘: {today.strftime('%Y-%m-%d')}
상황: {situation}
법령: {legal_basis}
이행/의견제출 기간은 며칠인가?
숫자만 출력. 모르겠으면 15.
"""
        try:
            res = (llm_service.generate_text(prompt) or "").strip()
            m = re.search(r"\d{1,3}", res)
            days = int(m.group(0)) if m else 15
            days = max(1, min(days, 180))
        except Exception:
            days = 15

        deadline = today + timedelta(days=days)
        return {
            "today_str": today.strftime("%Y. %m. %d."),
            "deadline_str": deadline.strftime("%Y. %m. %d."),
            "days_added": days,
            "doc_num": f"행정-{today.strftime('%Y')}-{int(time.time())%1000:03d}호",
        }

    @staticmethod
    def drafter(situation, legal_basis, meta_info, strategy):
        doc_schema = {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"},
                "receiver": {"type": "STRING"},
                "body_paragraphs": {"type": "ARRAY", "items": {"type": "STRING"}},
                "department_head": {"type": "STRING"},
            },
            "required": ["title", "receiver", "body_paragraphs", "department_head"],
        }

        prompt = f"""
당신은 행정기관의 베테랑 서기입니다. 아래 정보를 바탕으로 완결된 공문서를 작성하세요.

[입력]
- 민원: {situation}
- 법적 근거: {legal_basis}
- 시행일자: {meta_info['today_str']}
- 기한: {meta_info['deadline_str']} ({meta_info['days_added']}일)

[전략]
{strategy}

[원칙]
1) 본문에 법 조항 인용 필수
2) 구조: 경위 -> 법적 근거 -> 처분 내용 -> 이의제기 절차
3) 개인정보 마스킹('OOO')
"""
        return llm_service.generate_json(prompt, schema=doc_schema)


# ==========================================
# 5) Workflow
# ==========================================
def run_workflow(user_input):
    log_placeholder = st.empty()
    logs = []

    def add_log(msg, style="sys"):
        logs.append(f"<div class='agent-log log-{style}'>{_escape(msg)}</div>")
        log_placeholder.markdown("".join(logs), unsafe_allow_html=True)
        time.sleep(0.25)

    add_log("🔍 Phase 1: 법령 및 유사 사례 리서치 중...", "legal")
    legal_basis = LegalAgents.researcher(user_input)
    add_log("📜 법적 근거 발견 완료", "legal")

    add_log("🟩 네이버 검색 엔진 가동...", "search")
    try:
        search_results = search_service.search_precedents(user_input)
    except Exception:
        search_results = "검색 모듈 미연결 (건너뜀)"

    add_log("🧠 Phase 2: AI 주무관이 업무 처리 방향 수립...", "strat")
    strategy = LegalAgents.strategist(user_input, legal_basis, search_results)

    add_log("📅 Phase 3: 기한 산정 및 공문서 작성...", "calc")
    meta_info = LegalAgents.clerk(user_input, legal_basis)

    add_log("✍️ 최종 공문서 조판 중...", "draft")
    doc_data = LegalAgents.drafter(user_input, legal_basis, meta_info, strategy)

    time.sleep(0.4)
    log_placeholder.empty()

    return {
        "situation": user_input,
        "doc": doc_data,
        "meta": meta_info,
        "law": legal_basis,
        "search": search_results,
        "strategy": strategy,
    }


# ==========================================
# 6) Follow-up Chat (케이스 고정 + 필요 시 재조회)
# ==========================================
def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text

def build_case_context(res: dict) -> str:
    situation = res.get("situation", "")
    law_txt = _strip_html(res.get("law", ""))
    news_txt = _strip_html(res.get("search", ""))
    strategy = res.get("strategy", "")
    doc = res.get("doc") or {}

    body_paras = doc.get("body_paragraphs", [])
    if isinstance(body_paras, str):
        body_paras = [body_paras]
    body = "\n".join([f"- {p}" for p in body_paras])

    ctx = f"""
[케이스 컨텍스트]
1) 민원 상황(원문)
{situation}

2) 적용 법령/조문(이미 확인된 내용)
{law_txt}

3) 관련 뉴스/사례(이미 조회된 내용)
{news_txt}

4) 업무 처리 방향(Strategy)
{strategy}

5) 생성된 공문서(요약)
- 제목: {doc.get('title','')}
- 수신: {doc.get('receiver','')}
- 본문:
{body}
- 발신: {doc.get('department_head','')}

[규칙]
- 기본 답변은 위 컨텍스트 범위에서만 작성.
- 컨텍스트에 없는 법령/사례를 단정하지 말 것.
- 사용자가 “근거 더 / 다른 조문 / 뉴스 더” 요청하면 그때만 추가 조회(툴 호출).
"""
    return ctx.strip()

def needs_tool_call(user_msg: str) -> dict:
    t = user_msg.lower()
    law_triggers = ["근거", "조문", "법령", "몇 조", "원문", "현행", "추가 조항", "다른 조문", "전문", "절차법", "행정절차"]
    news_triggers = ["뉴스", "사례", "판례", "기사", "보도", "최근", "유사", "선례"]
    return {"need_law": any(k in t for k in law_triggers), "need_news": any(k in t for k in news_triggers)}

def plan_tool_calls_llm(user_msg: str, situation: str, known_law_text: str) -> dict:
    schema = {
        "type": "OBJECT",
        "properties": {
            "need_law": {"type": "BOOLEAN"},
            "law_name": {"type": "STRING"},
            "article_num": {"type": "INTEGER"},
            "need_news": {"type": "BOOLEAN"},
            "news_query": {"type": "STRING"},
            "reason": {"type": "STRING"},
        },
        "required": ["need_law", "law_name", "article_num", "need_news", "news_query", "reason"],
    }

    prompt = f"""
너는 행정업무 보조 에이전트다. 사용자의 후속 질문을 보고, 추가 조회가 필요하면 계획을 JSON으로 만든다.

[민원 상황]
{situation}

[이미 확보된 적용 법령 텍스트]
{known_law_text[:2500]}

[사용자 질문]
{user_msg}

[출력 규칙]
- 추가 법령 조회 필요: need_law=true, law_name=정식 법령명 1개, article_num=정수(모르면 0)
- 추가 뉴스 조회 필요: need_news=true, news_query=2~4단어 키워드(콤마 가능)
- 불필요하면 need_law/need_news=false
- 반드시 JSON만 출력
"""
    plan = llm_service.generate_json(prompt, schema=schema) or {}
    if not isinstance(plan, dict):
        return {"need_law": False, "law_name": "", "article_num": 0, "need_news": False, "news_query": "", "reason": "parse failed"}
    plan["article_num"] = int(plan.get("article_num") or 0)
    plan["law_name"] = str(plan.get("law_name") or "").strip()
    plan["news_query"] = str(plan.get("news_query") or "").strip()
    plan["reason"] = str(plan.get("reason") or "").strip()
    return plan

def answer_followup(case_context: str, extra_context: str, chat_history: list, user_msg: str) -> str:
    hist = chat_history[-8:]
    hist_txt = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in hist])

    prompt = f"""
너는 '케이스 고정 행정 후속 Q&A 챗봇'이다.

{case_context}

[추가 조회 결과(있으면)]
{extra_context if extra_context else "(없음)"}

[대화 히스토리(최근)]
{hist_txt if hist_txt else "(없음)"}

[사용자 질문]
{user_msg}

[답변 규칙]
- 케이스 컨텍스트/추가 조회 결과 범위에서만 답한다.
- 모르면 모른다고 하고, 필요한 추가 조회 종류(법령/뉴스)를 구체적으로 말한다.
- 서론 없이 실무형으로.
"""
    return llm_service.generate_text(prompt)

def render_followup_chat(res: dict):
    # 세션 초기화
    if "case_id" not in st.session_state:
        st.session_state["case_id"] = None
    if "followup_count" not in st.session_state:
        st.session_state["followup_count"] = 0
    if "followup_messages" not in st.session_state:
        st.session_state["followup_messages"] = []
    if "followup_extra_context" not in st.session_state:
        st.session_state["followup_extra_context"] = ""
    if "report_id" not in st.session_state:
        st.session_state["report_id"] = None

    current_case_id = (res.get("meta") or {}).get("doc_num", "") or "case"
    if st.session_state["case_id"] != current_case_id:
        st.session_state["case_id"] = current_case_id
        st.session_state["followup_count"] = 0
        st.session_state["followup_messages"] = []
        st.session_state["followup_extra_context"] = ""

    remain = max(0, MAX_FOLLOWUP_Q - st.session_state["followup_count"])
    st.info(f"후속 질문 가능 횟수: **{remain}/{MAX_FOLLOWUP_Q}**")

    if remain == 0:
        st.warning("후속 질문 한도(5회)를 모두 사용했습니다. (추가 질문 불가)")
        return

    # 기존 대화 렌더
    for m in st.session_state["followup_messages"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    user_q = st.chat_input("공문 결과를 바탕으로 후속 질문 (최대 5회)")
    if not user_q:
        return

    # user append
    st.session_state["followup_messages"].append({"role": "user", "content": user_q})
    st.session_state["followup_count"] += 1

    with st.chat_message("user"):
        st.markdown(user_q)

    case_context = build_case_context(res)

    # 필요 시에만 툴 호출
    extra_ctx = st.session_state.get("followup_extra_context", "")
    tool_need = needs_tool_call(user_q)

    if tool_need["need_law"] or tool_need["need_news"]:
        plan = plan_tool_calls_llm(user_q, res.get("situation", ""), _strip_html(res.get("law", "")))

        if plan.get("need_law") and plan.get("law_name"):
            art = plan.get("article_num", 0)
            art = art if art > 0 else None
            law_text, law_link = law_api_service.get_law_text(plan["law_name"], art, return_link=True)

            extra_ctx += f"\n\n[추가 법령 조회]\n- 요청: {plan['law_name']} / 제{art if art else '?'}조\n{_strip_html(law_text)}"
            if law_link:
                extra_ctx += f"\n(현행 원문 링크: {law_link})"

        if plan.get("need_news") and plan.get("news_query"):
            news_txt = search_service.search_news(plan["news_query"])
            extra_ctx += f"\n\n[추가 뉴스 조회]\n- 검색어: {plan['news_query']}\n{_strip_html(news_txt)}"

        st.session_state["followup_extra_context"] = extra_ctx

    # 답변 생성
    with st.chat_message("assistant"):
        with st.spinner("후속 답변 생성 중..."):
            ans = answer_followup(
                case_context=case_context,
                extra_context=st.session_state.get("followup_extra_context", ""),
                chat_history=st.session_state["followup_messages"],
                user_msg=user_q,
            )
            st.markdown(ans)

    st.session_state["followup_messages"].append({"role": "assistant", "content": ans})

    # ✅ DB에 후속까지 저장
    followup_payload = {
        "count": st.session_state["followup_count"],
        "messages": st.session_state["followup_messages"],
        "extra_context": st.session_state.get("followup_extra_context", ""),
    }
    upd = db_service.update_followup(
        report_id=st.session_state.get("report_id"),
        res=res,
        followup=followup_payload,
    )
    if not upd.get("ok"):
        st.caption(f"DB 후속 저장 실패: {upd.get('msg')}")


# ==========================================
# 7) UI
# ==========================================
def main():
    col_left, col_right = st.columns([1, 1.2])

    with col_left:
        st.title("🏢 AI 행정관 Pro 충주시청")
        st.caption("문의 kim0395kk@korea.kr \n 세계최초 행정 Govable AI 에이젼트 ")
        st.markdown("---")

        st.markdown("### 🗣️ 업무 지시")
        user_input = st.text_area(
            "업무 내용",
            height=150,
            placeholder="예시 \n- 상황: (무슨 일 / 어디 / 언제 / 증거 유무...).... \n- 의도: (확인하고 싶은 쟁점: 요건/절차/근거... )\n- 요청: (원하는 결과물: 공문 종류/회신/사전통지 등)",
            label_visibility="collapsed",
        )

        if st.button("⚡ 스마트 분석 시작", type="primary", use_container_width=True):
            if not user_input:
                st.warning("내용을 입력해주세요.")
            else:
                try:
                    with st.spinner("AI 에이전트 팀이 협업 중입니다..."):
                        res = run_workflow(user_input)

                        # ✅ workflow 결과 저장
                        ins = db_service.insert_initial_report(res)
                        res["save_msg"] = ins.get("msg")
                        st.session_state["report_id"] = ins.get("id")

                        st.session_state["workflow_result"] = res
                except Exception as e:
                    st.error(f"시스템 오류 발생: {e}")

        if "workflow_result" in st.session_state:
            res = st.session_state["workflow_result"]
            st.markdown("---")

            if "성공" in (res.get("save_msg") or ""):
                st.success(f"✅ {res['save_msg']}")
            else:
                st.info(f"ℹ️ {res.get('save_msg','')}")

            with st.expander("✅ [검토] 법령 및 유사 사례 확인", expanded=True):
                col1, col2 = st.columns(2)

                with col1:
                    st.markdown("**📜 적용 법령 (법령명 클릭 시 현행 원문 새창)**")
                    raw_law = res.get("law", "")

                    cleaned = raw_law.replace("&lt;", "<").replace("&gt;", ">")
                    cleaned = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", cleaned)

                    # ✅ markdown 링크 -> 새창 링크
                    cleaned = re.sub(
                        r'\[([^\]]+)\]\(([^)]+)\)',
                        r'<a href="\2" target="_blank" style="color:#2563eb; text-decoration:none; font-weight:700;">\1</a>',
                        cleaned,
                    )
                    cleaned = cleaned.replace("---", "<br><br>")
                    cleaned = cleaned.replace("\n", "<br>")

                    st.markdown(
                        f"""
                        <div style="
                            height: 300px;
                            overflow-y: auto;
                            padding: 15px;
                            border-radius: 8px;
                            border: 1px solid #e5e7eb;
                            background: #f8fafc;
                            font-family: 'Pretendard', sans-serif;
                            font-size: 0.9rem;
                            line-height: 1.6;
                            color: #334155;
                        ">
                        {cleaned}
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                with col2:
                    st.markdown("**🟩 관련 뉴스/사례**")
                    raw_news = res.get("search", "")

                    news_body = raw_news.replace("# ", "").replace("## ", "")
                    news_body = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", news_body)
                    news_html = re.sub(
                        r"\[([^\]]+)\]\(([^)]+)\)",
                        r'<a href="\2" target="_blank" style="color:#2563eb; text-decoration:none; font-weight:600;">\1</a>',
                        news_body
                    )
                    news_html = news_html.replace("\n", "<br>")

                    st.markdown(
                        f"""
                        <div style="
                            height: 300px;
                            overflow-y: auto;
                            padding: 15px;
                            border-radius: 8px;
                            border: 1px solid #dbeafe;
                            background: #eff6ff;
                            font-family: 'Pretendard', sans-serif;
                            font-size: 0.9rem;
                            line-height: 1.6;
                            color: #1e3a8a;
                        ">
                        {news_html}
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

            with st.expander("🧭 [방향] 업무 처리 가이드라인", expanded=True):
                st.markdown(res.get("strategy", ""))

    with col_right:
        if "workflow_result" in st.session_state:
            res = st.session_state["workflow_result"]
            doc = res.get("doc")
            meta = res.get("meta", {})

            if doc:
                html_content = f"""
<div class="paper-sheet">
  <div class="stamp">직인생략</div>
  <div class="doc-header">{_escape(doc.get('title', '공 문 서'))}</div>
  <div class="doc-info">
    <span>문서번호: {_escape(meta.get('doc_num',''))}</span>
    <span>시행일자: {_escape(meta.get('today_str',''))}</span>
    <span>수신: {_escape(doc.get('receiver', '수신자 참조'))}</span>
  </div>
  <hr style="border: 1px solid black; margin-bottom: 30px;">
  <div class="doc-body">
"""
                paragraphs = doc.get("body_paragraphs", [])
                if isinstance(paragraphs, str):
                    paragraphs = [paragraphs]

                for p in paragraphs:
                    html_content += f"<p style='margin-bottom: 15px;'>{_escape(p)}</p>"

                html_content += f"""
  </div>
  <div class="doc-footer">{_escape(doc.get('department_head', '행정기관장'))}</div>
</div>
"""
                st.markdown(html_content, unsafe_allow_html=True)

                # ✅ 후속 질문 위치: 공문 밑(요청사항)
                st.markdown("---")
                with st.expander("💬 [후속 질문] 케이스 고정 챗봇 (최대 5회)", expanded=True):
                    render_followup_chat(res)

            else:
                st.warning("공문 생성 결과(doc)가 비어 있습니다. (모델 JSON 출력 실패 가능)")

        else:
            st.markdown(
                """<div style='text-align: center; padding: 100px; color: #aaa; background: white; border-radius: 10px; border: 2px dashed #ddd;'>
<h3>📄 Document Preview</h3><p>왼쪽에서 업무를 지시하면<br>완성된 공문서가 여기에 나타납니다.</p></div>""",
                unsafe_allow_html=True,
            )

if __name__ == "__main__":
    main()

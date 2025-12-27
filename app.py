import streamlit as st
import google.generativeai as genai
import json
import re
from datetime import datetime, timedelta
import time

# --- 0. UI 설정: "결재판" 컨셉 ---
st.set_page_config(layout="wide", page_title="AI Bureau: One-Shot Action", page_icon="🏢")

st.markdown("""
<style>
    /* 배경: 차분한 오피스 톤 */
    .stApp { background-color: #f3f4f6; }
    
    /* 결과물: A4 용지 스타일 (프리뷰) */
    .paper-sheet {
        background-color: white;
        width: 100%;
        max-width: 210mm; /* A4 폭 */
        min-height: 297mm; /* A4 높이 */
        padding: 25mm;
        margin: auto;
        box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        font-family: 'Batang', serif; /* 명조체 (공문서 표준) */
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
    
    /* 에이전트 로그 스타일 */
    .agent-log { font-family: 'Consolas', monospace; font-size: 0.85rem; padding: 5px 10px; border-radius: 5px; margin-bottom: 5px; }
    .log-legal { background-color: #e0f2fe; color: #0369a1; border-left: 4px solid #0ea5e9; }
    .log-calc { background-color: #f0fdf4; color: #15803d; border-left: 4px solid #22c55e; }
    .log-draft { background-color: #fef2f2; color: #b91c1c; border-left: 4px solid #ef4444; }
</style>
""", unsafe_allow_html=True)

# --- 1. 인프라 (안정성) ---
try:
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    genai.configure(api_key=GEMINI_API_KEY)
    # [중요] 1.5 Flash 사용 (오류 최소화 + 속도)
    model = genai.GenerativeModel("gemini-1.5-flash")
except:
    st.error("API 키 오류: Secrets 설정을 확인하세요.")
    st.stop()

# --- 2. 멀티 에이전트 로직 (The 'Agents') ---

def agent_legal_researcher(situation):
    """[에이전트 1] 법률 분석가: 상황에 맞는 법령과 조항을 찾아냄"""
    prompt = f"""
    당신은 30년 경력의 법제관입니다.
    상황: "{situation}"
    
    위 상황에 적용할 가장 정확한 '법령명'과 '관련 조항'을 하나만 찾으시오.
    반드시 현행 대한민국 법령이어야 함.
    (예: 여권법 제00조, 도로교통법 제00조 등)
    """
    res = model.generate_content(prompt)
    return res.text.strip()

def agent_chief_clerk():
    """[에이전트 2] 주무관: 행정 절차 날짜 자동 계산"""
    today = datetime.now()
    # 통상적인 의견제출 기한 (15일 후)
    deadline = today + timedelta(days=15)
    
    return {
        "today_str": today.strftime("%Y. %m. %d."),
        "deadline_str": deadline.strftime("%Y. %m. %d."),
        "doc_num": f"행정-{today.strftime('%Y')}-{int(time.time())%1000:03d}호"
    }

def agent_drafter(situation, legal_basis, date_info):
    """[에이전트 3] 서기: 정보를 취합해 공문서 초안 작성"""
    
    prompt = f"""
    당신은 행정기관의 베테랑 서기입니다.
    다음 정보를 바탕으로 완결된 '공문(JSON)'을 작성하시오.
    
    [입력 정보]
    - 민원 상황: {situation}
    - 법적 근거: {legal_basis}
    - 시행 일자: {date_info['today_str']}
    - 기한: {date_info['deadline_str']}
    - 문서 번호: {date_info['doc_num']}
    
    [작성 원칙]
    1. 수신인이 명확하지 않으면 상황에 맞춰 'OOO 귀하' 또는 '차량소유주 귀하' 등으로 추론하여 기재.
    2. 본문은 [경과 및 원인] -> [법적 근거] -> [처분 내용] -> [권리 구제 절차] 순으로 논리 정연하게 작성.
    3. 톤앤매너: 정중하지만 단호한 행정 용어 사용.
    
    [출력 포맷(JSON)]
    {{
        "title": "여권 발급 거부 처분 사전 통지서 (예시임, 상황에 맞게 변경)",
        "receiver": "...",
        "body_paragraphs": [
            "1. 귀하의 무궁한 발전을 기원합니다.",
            "2. 귀하께서 신청하신...", 
            "3. 관련 법령({legal_basis})에 의거하여...",
            "4. 이에 따라..."
        ],
        "department_head": "OO시 여권민원과장"
    }}
    """
    try:
        res = model.generate_content(prompt)
        match = re.search(r'\{.*\}', res.text, re.DOTALL)
        return json.loads(match.group(0)) if match else None
    except:
        return None

# --- 3. 오케스트레이션 (The Action) ---

def execute_high_level_action(user_input):
    """에이전트들을 지휘하여 결과물 도출"""
    
    # UI: 에이전트 작업 로그 표시 컨테이너
    log_container = st.empty()
    
    def log(msg, type="legal"):
        # 실제 작업하는 것처럼 보이게 로그 출력
        log_container.markdown(f"<div class='agent-log log-{type}'>{msg}</div>", unsafe_allow_html=True)
        time.sleep(0.3) # 시각적 효과

    # Step 1: 법적 근거 확보
    log("👨‍⚖️ Legal Agent: 관련 법령 및 판례 검색 중...", "legal")
    legal_basis = agent_legal_researcher(user_input)
    log(f"✅ 법적 근거 확보: {legal_basis}", "legal")
    
    # Step 2: 날짜 및 행정 정보 계산
    log("📅 Clerk Agent: 행정절차법에 따른 기한 산정 중...", "calc")
    date_info = agent_chief_clerk()
    log(f"✅ 기한 설정: {date_info['today_str']} ~ {date_info['deadline_str']}", "calc")
    
    # Step 3: 문서 조판
    log("✍️ Drafter Agent: 공문서 조판 및 서식 적용 중...", "draft")
    final_doc = agent_drafter(user_input, legal_basis, date_info)
    
    log_container.empty() # 로그 삭제 (깔끔하게)
    return final_doc, date_info

# --- 4. 메인 화면 ---

col_left, col_right = st.columns([1, 1.2])

with col_left:
    st.title("🏢 AI Bureau")
    st.caption("The One-Shot Administrative Agent")
    st.markdown("---")
    
    st.markdown("### 🗣️ 업무 지시 (Instruction)")
    st.markdown("상황을 대화하듯 입력하세요. AI가 알아서 처리합니다.")
    
    user_input = st.text_area(
        "입력창",
        height=150,
        placeholder="예시: \n- 1년 전 사진으로 여권 만들겠다는 민원인 반려 공문 써줘.\n- 12가 3456 차량 두 달째 방치 중. 자진처리 명령서 만들어.",
        label_visibility="collapsed"
    )
    
    if st.button("⚡ 실행 (Execute)", type="primary", use_container_width=True):
        if not user_input:
            st.warning("지시 사항을 입력해주세요.")
        else:
            with st.spinner("에이전트들이 협업 중입니다..."):
                doc_data, meta_info = execute_high_level_action(user_input)
                st.session_state['result'] = (doc_data, meta_info)

    st.markdown("---")
    st.info("💡 **Tip:** 복잡한 양식이나 날짜를 입력할 필요가 없습니다. 상황만 던져주면 법령과 절차는 에이전트가 결정합니다.")

with col_right:
    # 결과물 프리뷰 영역
    if 'result' in st.session_state:
        doc, meta = st.session_state['result']
        
        if doc:
            # HTML로 A4 용지 렌더링
            html_content = f"""
            <div class="paper-sheet">
                <div class="stamp">직인생략</div>
                <div class="doc-header">{doc['title']}</div>
                <div class="doc-info">
                    <span>문서번호: {meta['doc_num']}</span>
                    <span>시행일자: {meta['today_str']}</span>
                    <span>수신: {doc['receiver']}</span>
                </div>
                <hr style="border: 1px solid black; margin-bottom: 30px;">
                <div class="doc-body">
            """
            
            for p in doc['body_paragraphs']:
                html_content += f"<p style='margin-bottom: 15px;'>{p}</p>"
            
            html_content += f"""
                </div>
                <div class="doc-footer">
                    {doc['department_head']}
                </div>
            </div>
            """
            
            st.markdown(html_content, unsafe_allow_html=True)
            
            # 진짜 액션: 다운로드 버튼
            st.download_button(
                label="🖨️ 출력/다운로드 (HTML)",
                data=html_content,
                file_name="공문.html",
                mime="text/html",
                use_container_width=True
            )
        else:
            st.error("문서 생성에 실패했습니다. 다시 시도해주세요.")
    else:
        # 대기 화면
        st.markdown("""
        <div style='text-align: center; padding: 100px; color: #aaa;'>
            <h3>📄 Ready to Draft</h3>
            <p>왼쪽에서 업무를 지시하면<br>여기에 완성된 문서가 나타납니다.</p>
        </div>
        """, unsafe_allow_html=True)


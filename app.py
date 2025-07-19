import streamlit as st
import re
import requests
import json

# --- 페이지 기본 설정 ---
st.set_page_config(
    page_title="AI 문서 교정 시스템",
    page_icon="✨",
    layout="wide"
)

# --- Aurora Glass UI 디자인 적용 (CSS) ---
aurora_glass_css = """
<style>
    /* Aurora Background Animation */
    @keyframes aurora {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }

    /* 전체 폰트 및 배경 설정 */
    html, body, [class*="st-"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji";
    }

    /* 메인 앱 컨테이너에 오로라 배경 적용 */
    .stApp {
        background: #111827; /* 다크 네이비 배경 */
        background: linear-gradient(125deg, #111827 0%, #1F2937 40%, #374151 70%, #4B5563 100%);
        background-size: 400% 400%;
        animation: aurora 15s ease infinite;
        color: #F9FAFB;
    }

    /* 메인 타이틀 */
    h1 {
        font-size: 44px !important;
        font-weight: 700 !important;
        color: #FFFFFF;
        text-align: center;
        padding-top: 40px;
        letter-spacing: -1px;
    }

    /* 서브 타이틀 */
    .subtitle {
        font-size: 20px;
        font-weight: 400;
        color: #9CA3AF;
        text-align: center;
        margin-bottom: 50px;
    }

    /* 섹션 헤더 (1. 원본, 2. 수정된) */
    h2 {
        font-size: 22px !important;
        font-weight: 600 !important;
        color: #E5E7EB;
        border: none !important;
        padding-bottom: 15px !important;
    }
    
    h3 {
        font-size: 22px !important;
        font-weight: 600 !important;
        color: #E5E7EB;
        padding-top: 40px;
    }

    /* Glassmorphism 효과를 위한 컨테이너 */
    .glass-container {
        background: rgba(31, 41, 55, 0.5); /* 반투명 배경 */
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        border-radius: 16px;
        border: 1px solid rgba(255, 255, 255, 0.1);
        padding: 25px;
    }

    /* 텍스트 입력창 및 결과창 스타일 */
    .stTextArea textarea, .result-container {
        background-color: rgba(17, 24, 39, 0.8);
        border: 1px solid #4B5563;
        border-radius: 12px;
        color: #F9FAFB;
        font-size: 16px;
        padding: 15px;
        transition: border-color 0.3s, box-shadow 0.3s;
    }
    .stTextArea textarea:focus {
        border-color: #3B82F6;
        box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25);
    }
    .result-container {
        height: 430px; 
        overflow-y: scroll;
        line-height: 1.6;
    }


    /* 버튼 스타일 */
    .stButton>button {
        border: none;
        border-radius: 12px;
        padding: 14px 24px;
        font-size: 16px;
        font-weight: 600;
        transition: all 0.2s ease-in-out;
        width: 100%;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
    }
    .stButton>button:active {
        transform: translateY(0px);
    }

    /* 1단계 버튼 (유리 스타일) */
    div[data-testid="stButton"]:nth-of-type(1) > button {
        background: rgba(255, 255, 255, 0.1);
        color: #F9FAFB;
        border: 1px solid rgba(255, 255, 255, 0.2);
    }
    div[data-testid="stButton"]:nth-of-type(1) > button:hover {
        background: rgba(255, 255, 255, 0.15);
    }

    /* 2단계 버튼 (파란색) */
    div[data-testid="stButton"]:nth-of-type(2) > button {
        background-color: #3B82F6;
        color: #FFFFFF;
    }
    div[data-testid="stButton"]:nth-of-type(2) > button:hover {
        background-color: #2563EB;
    }
    div[data-testid="stButton"]:nth-of-type(2) > button:disabled {
        background-color: rgba(55, 65, 81, 0.5);
        color: #9CA3AF;
        cursor: not-allowed;
    }

    /* 변경사항 목록 스타일 */
    .change-card {
        background-color: rgba(17, 24, 39, 0.7);
        border: 1px solid #4B5563;
        border-radius: 12px;
        padding: 15px;
        margin-bottom: 10px;
    }
    .change-card-rule { border-left: 4px solid #FBBF24; }
    .change-card-ai { border-left: 4px solid #3B82F6; }
    .change-card .description { font-weight: 500; color: #E5E7EB; }

    /* Streamlit 기본 UI 숨기기 */
    header, #MainMenu, footer { visibility: hidden; }
</style>
"""
st.markdown(aurora_glass_css, unsafe_allow_html=True)

# --- 교정 로직 함수들 (기능은 이전과 동일) ---

def run_rule_based_corrections(text):
    correction_rules = [
        {'original': re.compile(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일'), 'corrected': r'\1. \2. \3.', 'description': '날짜 형식 표준화 (YYYY. MM. DD.)'},
        {'original': re.compile(r'(\d{1,2})\s*시\s*(\d{1,2})\s*분'), 'corrected': r'\1:\2', 'description': '시간 형식 표준화 (HH:MM)'},
        {'original': re.compile(r'^\s*첫째, ?', re.MULTILINE), 'corrected': '1. ', 'description': '항목 번호 체계: "첫째," -> "1."'},
        {'original': re.compile(r'^\s*둘째, ?', re.MULTILINE), 'corrected': '2. ', 'description': '항목 번호 체계: "둘째," -> "2."'},
        {'original': re.compile(r'^\s*셋째, ?', re.MULTILINE), 'corrected': '3. ', 'description': '항목 번호 체계: "셋째," -> "3."'},
        {'original': re.compile(r'^\s*(\d+)\s*\)', re.MULTILINE), 'corrected': r'\1.', 'description': '항목 번호 체계: "1)" -> "1."'},
        {'original': re.compile(r'^\s*\(\s*([가-힣])\s*\)', re.MULTILINE), 'corrected': r'\1.', 'description': '항목 번호 체계: "(가)" -> "가."'},
        {'original': re.compile(r'^\s*([가-힣])\s*\)', re.MULTILINE), 'corrected': r'\1.', 'description': '항목 번호 체계: "가)" -> "가."'},
        {'original': re.compile(r'^붙임\s*:'), 'corrected': '붙임  ', 'description': '"붙임:" -> "붙임  " (2칸 띄움)'},
        {'original': re.compile(r'시건장치'), 'corrected': '잠금장치', 'description': '일본식 용어: "시건장치" -> "잠금장치"'},
        {'original': re.compile(r'금일'), 'corrected': '오늘', 'description': '일본식 한자어: "금일" -> "오늘"'},
        {'original': re.compile(r'명일'), 'corrected': '내일', 'description': '일본식 한자어: "명일" -> "내일"'},
        {'original': re.compile(r'익일'), 'corrected': '다음 날', 'description': '일본식 한자어: "익일" -> "다음 날"'},
        {'original': re.compile(r'요망합니다'), 'corrected': '하시기 바랍니다', 'description': '권위적 표현: "요망합니다" -> "하시기 바랍니다"'},
        {'original': re.compile(r'바랍니다\s*\.'), 'corrected': '바랍니다.', 'description': '권위적 표현: "바랍니다." -> "바랍니다."'},
        {'original': re.compile(r'\s+([.,?!%℃°])'), 'corrected': r'\1', 'description': '문장 부호 앞 불필요한 공백 제거'},
        {'original': re.compile(r'([.,?!])(?=[가-힣A-Za-z0-9])'), 'corrected': r'\1 ', 'description': '문장 부호 뒤 공백 추가'},
        {'original': re.compile(r'(제)\s*(\d+)\s*(조)'), 'corrected': r'\1\2\3', 'description': '법률 조항 붙여쓰기 (제 O 조 -> 제O조)'},
        {'original': re.compile(r'(제)\s*(\d+)\s*(항)'), 'corrected': r'\1\2\3', 'description': '법률 조항 붙여쓰기 (제 O 항 -> 제O항)'},
        {'original': re.compile(r'(?<!\S)(및|등)(?!\S)'), 'corrected': r' \1 ', 'description': '"및", "등" 양쪽 띄어쓰기'},
        {'original': re.compile(r'(\d)\s*,\s*(\d)'), 'corrected': r'\1,\2', 'description': '쉼표(,) 주변 공백 제거'},
    ]
    corrected_text = text
    changes = []
    for rule in correction_rules:
        new_text, count = rule['original'].subn(rule['corrected'], corrected_text)
        if count > 0:
            changes.append({'description': rule['description'], 'type': 'rule'})
            corrected_text = new_text

    lines = corrected_text.strip().split('\n')
    last_line = lines[-1].strip()
    if not last_line.startswith('붙임') and not last_line.endswith('끝.'):
        changes.append({'description': '본문 마지막에 "끝." 추가', 'type': 'rule'})
        corrected_text = corrected_text.strip() + '\n\n끝.'
    return corrected_text, changes

def run_ai_corrections(text):
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        st.error("오류: Gemini API 키가 설정되지 않았습니다. 관리자에게 문의하세요.")
        return None, []

    prompt = f"""
당신은 대한민국 행정안전부 소속의 1급 공무원이자, 최고의 공문서 작성 및 검토 전문가입니다.
당신의 임무는 아래 텍스트를 '행정업무운영 편람'에 명시된 대한민국 공문서 표준 지침에 따라 완벽하게 교정하는 것입니다.

[교정 원칙]
1.  **명확성**: 의미가 모호하거나 중의적으로 해석될 수 있는 부분을 명확하게 수정합니다.
2.  **간결성**: 불필요한 미사여구나 반복되는 표현을 제거하고, 문장을 간결하게 다듬습니다.
3.  **객관성**: 주관적인 감정이나 추측을 배제하고, 사실에 기반한 객관적인 표현을 사용합니다.
4.  **정확성**: 법령, 용어, 숫자, 띄어쓰기 등 모든 요소를 정확하게 교정합니다.
5.  **통일성**: 어조와 문체(-습니다, -합니다 등)를 일관되게 유지하고, 공문서로서의 품격을 갖추도록 합니다.
6.  **어휘 선택**: 어려운 한자어나 외래어 대신 알기 쉬운 우리말을 우선적으로 사용하되, 법률 용어나 전문 용어는 정확하게 사용합니다.

[수정할 텍스트]
{text}

[출력 형식]
결과는 반드시 아래의 JSON 형식으로만 응답해야 하며, 다른 부가 설명은 절대 추가하지 마십시오.
'changes' 배열에는 당신이 수정한 가장 핵심적인 내용만 몇 가지 포함시켜 주십시오.

\`\`\`json
{{
  "revised_text": "여기에 완벽하게 수정된 전체 텍스트를 넣어주세요.",
  "changes": [
    {{
      "original": "바꾸기 전 핵심 단어/구문",
      "corrected": "바꾼 후 핵심 단어/구문",
      "reason": "수정한 이유를 '명확성 개선', '간결성 확보' 등 교정 원칙에 기반하여 간결하게 설명"
    }}
  ]
}}
\`\`\`
"""
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {'Content-Type': 'application/json'}
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

    try:
        response = requests.post(api_url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        result = response.json()
        raw_json = result['candidates'][0]['content']['parts'][0]['text']
        cleaned_json = raw_json.replace("```json", "").replace("```", "").strip()
        ai_result = json.loads(cleaned_json)
        ai_changes = [{'description': f"'{c['original']}' -> '{c['corrected']}' ({c['reason']})", 'type': 'ai'} for c in ai_result.get('changes', [])]
        return ai_result.get('revised_text'), ai_changes
    except requests.exceptions.RequestException as e:
        st.error(f"API 요청 오류: {e}")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        st.error(f"API 응답 처리 오류: {e}")
    return None, []

# --- Streamlit UI 및 로직 구성 ---

# 세션 상태 초기화
if 'step' not in st.session_state:
    st.session_state.step = 0
    st.session_state.corrected_text = "수정된 내용이 여기에 표시됩니다."
    st.session_state.all_changes = []
    st.session_state.original_text_input = ""

# 헤더
st.markdown("<h1>AI 문서 교정 시스템</h1>", unsafe_allow_html=True)
st.markdown("<p class='subtitle'>행정안전부 표준 지침에 따라 AI가 기안문을 검토하고 교정합니다.</p>", unsafe_allow_html=True)


col1, col2 = st.columns(2, gap="large")

with col1:
    st.markdown('<div class="glass-container">', unsafe_allow_html=True)
    st.subheader("1. 원본 입력")
    st.text_area("이곳에 검토할 문서 내용을 붙여넣으세요.", height=350, key="original_text_input", label_visibility="collapsed")
    
    # 버튼 로직
    b_col1, b_col2 = st.columns(2)
    with b_col1:
        rule_check_button = st.button("1단계: 기본 규칙 검사")
    with b_col2:
        ai_check_button = st.button("2단계: AI로 다듬기", disabled=(st.session_state.step < 1))
    
    st.markdown('</div>', unsafe_allow_html=True)


if rule_check_button:
    if st.session_state.original_text_input:
        with st.spinner("기본 규칙을 검사하고 있습니다..."):
            corrected, changes = run_rule_based_corrections(st.session_state.original_text_input)
            st.session_state.corrected_text = corrected
            st.session_state.all_changes = changes
            st.session_state.step = 1
            st.rerun()
    else:
        st.warning("먼저 내용을 입력해주세요.")

if ai_check_button:
    if st.session_state.step >= 1:
        with st.spinner("AI가 문서를 다듬고 있습니다. 잠시만 기다려주세요..."):
            ai_corrected, ai_changes = run_ai_corrections(st.session_state.corrected_text)
            if ai_corrected is not None:
                st.session_state.corrected_text = ai_corrected
                st.session_state.all_changes.extend(ai_changes)
                st.session_state.step = 2
                st.rerun()
    else:
        st.warning("1단계 기본 규칙 검사를 먼저 실행해주세요.")

with col2:
    st.markdown('<div class="glass-container">', unsafe_allow_html=True)
    st.subheader("2. 수정된 내용")
    st.markdown(f'<div class="result-container">{st.session_state.corrected_text.replace(chr(10), "<br>")}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


# 변경 사항 목록
if st.session_state.all_changes:
    st.divider()
    st.subheader("주요 변경 사항")
    for change in st.session_state.all_changes:
        card_class = "change-card-rule" if change['type'] == 'rule' else "change-card-ai"
        st.markdown(f"""
        <div class="change-card {card_class}">
            <span class="description">{change['description']}</span>
        </div>
        """, unsafe_allow_html=True)


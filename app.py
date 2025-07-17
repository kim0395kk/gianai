import streamlit as st
import re
import requests
import json

# --- 페이지 기본 설정 ---
st.set_page_config(
      page_title="AI 공무원 문서 검토 도우미",
    page_icon=":writing_hand:", # ✍️ 이모지를 텍스트 코드로 변경
    layout="wide"
)
# --- 규칙 기반 교정 로직 ---
# 자바스크립트의 규칙을 파이썬 정규식으로 변환
correction_rules = [
    {'original' re.compile(r'(d{4})s년s(d{1,2})s월s(d{1,2})s일'), 'corrected' r'1. 2. 3.', 'description' '날짜 형식 표준화'},
    {'original' re.compile(r'(d{1,2})s시s(d{1,2})s분'), 'corrected' r'12', 'description' '시간 형식 표준화'},
    {'original' re.compile(r'^s첫째, ', re.MULTILINE), 'corrected' '1. ', 'description' '항목 번호 첫째, → 1.'},
    {'original' re.compile(r'^s둘째, ', re.MULTILINE), 'corrected' '2. ', 'description' '항목 번호 둘째, → 2.'},
    {'original' re.compile(r'^s셋째, ', re.MULTILINE), 'corrected' '3. ', 'description' '항목 번호 셋째, → 3.'},
    {'original' re.compile(r'^붙임s', re.MULTILINE), 'corrected' '붙임  ', 'description' '붙임 → 붙임   (2칸 띄움)'},
    {'original' re.compile(r'시건장치'), 'corrected' '잠금장치', 'description' '일본식 용어 수정'},
    {'original' re.compile(r'금일'), 'corrected' '오늘', 'description' '일본식 한자어 수정'},
    {'original' re.compile(r'명일'), 'corrected' '내일', 'description' '일본식 한자어 수정'},
    {'original' re.compile(r'요망합니다'), 'corrected' '하시기 바랍니다', 'description' '권위적 표현 수정'},
    {'original' re.compile(r's+([.,!%℃°])'), 'corrected' r'1', 'description' '문장 부호 앞 공백 제거'},
    {'original' re.compile(r'([.,!])([가-힣A-Za-z0-9])'), 'corrected' r'1 2', 'description' '문장 부호 뒤 공백 추가'},
    {'original' re.compile(r'(제)(d+)(조)'), 'corrected' r'123', 'description' '제 00 조 → 제00조'},
    {'original' re.compile(r'(제)(d+)(항)'), 'corrected' r'123', 'description' '제 00 항 → 제00항'},
]

def run_rule_based_corrections(text)
    규칙 기반으로 텍스트를 교정하고 변경사항을 반환합니다.
    corrected_text = text
    changes = []
    for rule in correction_rules
        # re.subn은 변경된 횟수도 반환합니다.
        new_text, count = rule['original'].subn(rule['corrected'], corrected_text)
        if count  0
            # 변경이 발생한 경우, 변경 내역을 기록 (정확한 원본수정본 추적은 복잡하므로 설명 위주로 기록)
            changes.append({
                'description' rule['description'],
                'type' 'rule'
            })
            corrected_text = new_text

    # 끝. 처리
    lines = corrected_text.strip().split('n')
    last_line = lines[-1].strip()
    if not last_line.startswith('붙임') and not last_line.endswith('끝.')
        changes.append({
            'description' '본문 마지막에 끝. 추가',
            'type' 'rule'
        })
        corrected_text = corrected_text.strip() + 'nn끝.'
    
    return corrected_text, changes

def highlight_text(text, all_changes)
    수정된 텍스트에 하이라이트를 적용합니다.
    # Streamlit은 복잡한 HTML 하이라이팅이 어려우므로, 간단한 마크다운으로 표현
    # 여기서는 단순 텍스트로 반환하고, AI 변경사항은 별도 목록으로 보여주는 방식을 택합니다.
    # 실제로는 더 정교한 방법이 필요할 수 있습니다.
    return text.replace('n', 'br')

# --- AI 교정 로직 ---
def run_ai_corrections(text)
    Gemini API를 호출하여 AI 교정을 수행합니다.
    api_key = st.secrets.get(GEMINI_API_KEY)
    if not api_key
        st.error(오류 Gemini API 키가 설정되지 않았습니다. 관리자에게 문의하세요.)
        return None, []

    prompt = f당신은 대한민국 공문서 작성 전문가입니다. 다음 텍스트를 아래의 조건에 맞게 수정해주세요.

1.  문맥에 맞는 어휘 문맥을 파악하여 더 적절하고 전문적인 공문서용 어휘로 변경해주세요.
2.  유의어 대체 불필요하게 반복되는 단어가 있다면, 문맥에 맞는 다른 유의어로 자연스럽게 교체해주세요.
3.  결과 형식 결과는 반드시 아래의 JSON 형식으로만 응답해주세요. 다른 설명은 절대 추가하지 마세요.

```json
{{
  revised_text 여기에 수정된 전체 텍스트를 넣어주세요.,
  changes [
    {{
      original 바꾸기 전 단어구문,
      corrected 바꾼 후 단어구문,
      reason 수정한 이유를 간결하게 설명 (예 어휘 개선, 동어 반복 회피 등)
    }}
  ]
}}
```

---
[수정할 텍스트]
{text}
---

    
    payload = {contents [{parts [{text prompt}]}]}
    headers = {'Content-Type' 'applicationjson'}
    api_url = fhttpsgenerativelanguage.googleapis.comv1betamodelsgemini-2.0-flashgenerateContentkey={api_key}

    try
        response = requests.post(api_url, headers=headers, data=json.dumps(payload))
        response.raise_for_status() # HTTP 오류 발생 시 예외 발생
        result = response.json()
        
        raw_json = result['candidates'][0]['content']['parts'][0]['text']
        cleaned_json = raw_json.replace(```json, ).replace(```, ).strip()
        ai_result = json.loads(cleaned_json)
        
        ai_changes = [{'description' f'{c['original']}' -> '{c['corrected']}' ({c['reason']}), 'type' 'ai'} for c in ai_result.get('changes', [])]
        
        return ai_result.get('revised_text'), ai_changes

    except requests.exceptions.RequestException as e
        st.error(fAPI 요청 오류 {e})
    except (KeyError, IndexError, json.JSONDecodeError) as e
        st.error(fAPI 응답 처리 오류 {e})
    
    return None, []


# --- Streamlit UI 구성 ---
st.title(":writing_hand: AI 공무원 문서 검토 도우미 (Streamlit ver.))
st.markdown(규칙 기반 검사와 AI의 지능형 교정으로 문서의 완성도를 높여보세요.)

# 세션 상태 초기화
if 'step' not in st.session_state
    st.session_state.step = 0
    st.session_state.original_text = 
    st.session_state.corrected_text = 
    st.session_state.all_changes = []

col1, col2 = st.columns(2)

with col1
    st.subheader(1. 원본 내용 입력)
    original_text_input = st.text_area(이곳에 검토할 문서 내용을 붙여넣으세요., height=400, key=original_text_area)
    
    rule_check_button = st.button(1단계 기본 규칙 검사, use_container_width=True, type=secondary)
    ai_check_button = st.button(2단계 AI로 다듬기, use_container_width=True, type=primary, disabled=(st.session_state.step  1))

    if rule_check_button
        if original_text_input
            st.session_state.original_text = original_text_input
            with st.spinner(기본 규칙을 검사하고 있습니다...)
                corrected, changes = run_rule_based_corrections(original_text_input)
                st.session_state.corrected_text = corrected
                st.session_state.all_changes = changes
                st.session_state.step = 1
        else
            st.warning(먼저 내용을 입력해주세요.)

    if ai_check_button
        if st.session_state.step = 1
            with st.spinner(AI가 문서를 다듬고 있습니다. 잠시만 기다려주세요...)
                ai_corrected, ai_changes = run_ai_corrections(st.session_state.corrected_text)
                if ai_corrected is not None
                    st.session_state.corrected_text = ai_corrected
                    st.session_state.all_changes.extend(ai_changes)
                    st.session_state.step = 2
        else
            st.warning(1단계 기본 규칙 검사를 먼저 실행해주세요.)


with col2
    st.subheader(2. 수정된 내용 확인)
    # HTML 렌더링을 위해 unsafe_allow_html=True 사용
    corrected_html = f'div style=background-color#f8f9fa; border 1px solid #e9ecef; border-radius 5px; padding 10px; height 400px; overflow-y scroll;{st.session_state.corrected_text.replace(chr(10), br)}div'
    st.markdown(corrected_html, unsafe_allow_html=True)
    st.code(st.session_state.corrected_text, language=None)


st.divider()

st.subheader(f변경 사항 목록 ({len(st.session_state.all_changes)}개))

if not st.session_state.all_changes
    st.info(아직 변경된 내용이 없습니다.)
else
    for change in st.session_state.all_changes
        if change['type'] == 'rule'
            st.markdown(f- [규칙] {change['description']})
        elif change['type'] == 'ai'
            st.markdown(f- [AI] {change['description']})

import streamlit as st
import time
import json
import re
from datetime import datetime
from supabase import create_client
import google.generativeai as genai
# (필요 시 serpapi, groq 등 import 유지)

# --- 0. System Config & Style ---
st.set_page_config(layout="wide", page_title="Google-grade AI Admin", page_icon="🧠")

st.markdown("""
<style>
    .stApp { background-color: #f0f2f6; }
    .thought-process { font-size: 0.85rem; color: #5f6368; border-left: 3px solid #dfe1e5; padding-left: 10px; margin: 5px 0; }
    .final-answer { background: white; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
    .action-card { border: 2px solid #4285f4; background-color: #e8f0fe; padding: 20px; border-radius: 10px; margin-top: 20px; }
    .log-entry { font-family: monospace; font-size: 0.8rem; background: #202124; color: #00ff00; padding: 10px; border-radius: 5px; margin-bottom: 5px; }
</style>
""", unsafe_allow_html=True)

# --- 1. Infrastructure Setup ---
try:
    # Secrets 로드 (예외처리 생략)
    GEMINI_API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
    
    genai.configure(api_key=GEMINI_API_KEY)
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    model = genai.GenerativeModel("gemini-1.5-pro") # Reasoning에 강한 Pro 모델 사용
except:
    st.error("시스템 구성 요소(API Key 등)가 누락되었습니다.")
    st.stop()

# --- 2. The 'Brain' Components (Atomized Logic) ---

def step_1_intent_parsing(situation):
    """[사고 1단계] 상황을 해체하고 핵심 의도를 파악"""
    prompt = f"""
    Acting as a Senior Legal Analyst, parse the following user situation.
    Identify: 1) Core Complaint, 2) Key Legal Entities, 3) Hidden Intent/Urgency.
    Return strictly in JSON.
    Situation: {situation}
    """
    res = model.generate_content(prompt)
    try:
        return json.loads(re.search(r'\{.*\}', res.text, re.DOTALL).group(0))
    except:
        return {"error": "Parsing Failed"}

def step_2_legal_retrieval(keywords):
    """[사고 2단계] 관련 법령 및 판례 검색 (Simulated)"""
    # 실제로는 여기서 Law API, Vector DB, SerpAPI를 병렬로 호출
    time.sleep(1) # 검색 시뮬레이션
    return f"검색된 법령: {keywords} 관련 법, 시행령, 지침 및 유권해석 사례"

def step_3_reasoning_engine(parsed_data, legal_data):
    """[사고 3단계] 법리와 현실 간의 충돌 분석 및 논리 구성 (The Core)"""
    prompt = f"""
    Perform a deep legal reasoning.
    Context: {parsed_data}
    Law: {legal_data}
    
    Task:
    1. Analyze the gap between the user's situation and the law.
    2. Determine if there is discretionary power (재량권) or strict regulation.
    3. Formulate a logical defense or rejection strategy.
    
    Output a concise reasoning summary (Korean).
    """
    res = model.generate_content(prompt)
    return res.text

def step_4_action_architect(reasoning_result):
    """[사고 4단계] 실무 처리를 위한 최적의 UI/UX 도구 설계 (A2UI)"""
    prompt = f"""
    Based on this reasoning: "{reasoning_result}"
    
    Design the most effective 'Action Tool' for the officer.
    If a document is needed, build a 'doc_builder'.
    If a phone call/check is needed, build a 'checklist'.
    
    Output strictly A2UI JSON format.
    Example: {{ "type": "doc_builder", "title": "...", "fields": [...], "template": "..." }}
    """
    res = model.generate_content(prompt)
    try:
        return json.loads(re.search(r'\{.*\}', res.text, re.DOTALL).group(0))
    except:
        return None

# --- 3. Orchestrator (The CEO's View) ---

def run_deep_thinking_pipeline(user_input):
    """모든 사고 과정을 관장하는 오케스트레이터"""
    
    # UI: 사고 과정을 실시간으로 보여주는 컨테이너
    with st.status("🧠 Deep Thinking Process Running...", expanded=True) as status:
        
        # Step 1
        st.write("1️⃣ **Intent Analysis:** 민원 내용의 의미론적 분석 중...")
        intent = step_1_intent_parsing(user_input)
        st.markdown(f"<div class='thought-process'>→ 감지된 의도: {intent.get('Core Complaint', 'N/A')}</div>", unsafe_allow_html=True)
        time.sleep(0.5)
        
        # Step 2
        st.write("2️⃣ **Legal Retrieval:** 법령 데이터베이스 및 판례 크롤링...")
        legal_context = step_2_legal_retrieval(intent.get('Key Legal Entities', '일반 행정'))
        st.markdown(f"<div class='thought-process'>→ 확보된 데이터: {legal_context[:50]}...</div>", unsafe_allow_html=True)
        
        # Step 3
        st.write("3️⃣ **Logic Synthesis:** 법리 해석 및 솔루션 도출 (추론 엔진 가동)...")
        reasoning = step_3_reasoning_engine(intent, legal_context)
        st.markdown(f"<div class='thought-process'>→ 추론 결론: {reasoning[:60]}...</div>", unsafe_allow_html=True)
        
        # Step 4
        st.write("4️⃣ **Action Engineering:** 최적의 업무 처리 도구(A2UI) 설계 중...")
        action_plan = step_4_action_architect(reasoning)
        
        status.update(label="✅ 분석 및 설계 완료!", state="complete", expanded=False)
        
    return reasoning, action_plan

# --- 4. Presentation & Interaction Layer ---

st.title("🏛️ Google-grade AI Admin System")
st.caption("Deep Reasoning Pipeline v2.0 | Powered by Gemini 1.5 Pro")

col_log, col_main = st.columns([1, 3])

with col_log:
    st.subheader("📡 System Logs")
    # DB 실시간 로그 (최근 3개)
    try:
        logs = supabase.table("action_logs").select("*").order("created_at", desc=True).limit(3).execute()
        for log in logs.data:
            st.markdown(f"<div class='log-entry'>[Time: {log['created_at'][11:19]}]<br>Action: {log['action_type']}</div>", unsafe_allow_html=True)
    except:
        st.caption("DB 연결 대기중...")

with col_main:
    situation = st.text_area("민원 상황 입력 (복잡한 케이스일수록 좋습니다)", height=120)
    
    if st.button("🚀 Execute Deep Analysis", type="primary"):
        if not situation:
            st.warning("내용을 입력해주세요.")
            st.stop()
            
        # 파이프라인 가동
        reasoning_result, action_tools = run_deep_thinking_pipeline(situation)
        
        st.divider()
        
        # [결과 화면 1] 논리적 분석 보고서
        st.subheader("📑 전략 분석 보고서")
        with st.container():
            st.markdown(f"""
            <div class="final-answer">
                {reasoning_result}
            </div>
            """, unsafe_allow_html=True)

        # [결과 화면 2] A2UI 액션 센터 (실무 도구)
        if action_tools:
            st.subheader("⚡ Action Center")
            st.markdown(f"""
            <div class="action-card">
                <h4 style="margin:0; color:#155724;">{action_tools.get('title')}</h4>
                <p style="font-size:0.9rem;">{action_tools.get('description', '업무 처리를 위한 도구입니다.')}</p>
            </div>
            """, unsafe_allow_html=True)
            
            # Dynamic Form Rendering
            with st.form("dynamic_action_form"):
                inputs = {}
                # JSON 정의에 따라 입력 필드 동적 생성
                cols = st.columns(2)
                fields = action_tools.get('fields', [])
                for i, field in enumerate(fields):
                    with cols[i % 2]:
                        inputs[field['id']] = st.text_input(field['label'])
                
                # [Action의 핵심] 저장 및 전송
                confirm_btn = st.form_submit_button("💾 승인 및 시스템 처리 (Save to DB)")
            
            if confirm_btn:
                # 1. 문서 완성 (Template Processing)
                final_doc = action_tools.get('template', "")
                for k, v in inputs.items():
                    final_doc = final_doc.replace(f"[{k}]", v)
                
                # 2. DB 저장 트랜잭션 (Commit)
                try:
                    payload = {
                        "situation_summary": situation[:50],
                        "action_type": action_tools.get('title'),
                        "input_data": inputs,
                        "generated_doc": final_doc,
                        "timestamp": datetime.now().isoformat()
                    }
                    supabase.table("action_logs").insert(payload).execute()
                    
                    st.success("시스템 처리 완료. 데이터베이스에 안전하게 기록되었습니다.")
                    st.toast("✅ Action Committed!")
                    time.sleep(1)
                    st.rerun() # 로그 갱신을 위해 리로드
                    
                except Exception as e:
                    st.error(f"DB 트랜잭션 실패: {e}")

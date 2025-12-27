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
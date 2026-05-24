"""
Agentic IAM — Streamlit Frontend
Run with: streamlit run frontend/app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Agentic IAM",
    page_icon="🔐",
    layout="wide",
    initial_sidebar_state="expanded",
)

with st.sidebar:
    st.markdown("## 🔐 Agentic IAM")
    st.caption("AI-driven Identity & Access Management")
    st.divider()
    from shared.config import config
    env_color = {"dev": "🟡", "staging": "🟠", "prod": "🔴"}.get(config.environment, "⚪")
    st.caption(f"{env_color} Environment: **{config.environment.upper()}**")
    if config.skip_approval:
        st.warning("⚠ SKIP_APPROVAL is ON", icon="⚠️")

st.title("🔐 Agentic IAM")
st.markdown("AI-driven Identity & Access Management. Navigate using the sidebar or the links below.")
st.divider()

col1, col2, col3 = st.columns(3)
col1.page_link("pages/1_dashboard.py",             label="📊 Dashboard",             icon="📊")
col2.page_link("pages/2_user_management.py",       label="👤 User AI Agent",       icon="👤")
col3.page_link("pages/3_app_management.py",        label="🖥️ Application AI Agent", icon="🖥️")

col4, col5, _ = st.columns(3)
col4.page_link("pages/4_action_ai_agent.py",       label="🤖 Action AI Agent",       icon="🤖")
col5.page_link("pages/5_system_configuration.py",  label="⚙️ System Configuration",  icon="⚙️")

st.divider()
st.caption("System Configuration includes the Audit Log under its second tab.")


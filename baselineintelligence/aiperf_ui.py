# aiperf_ui.py

import streamlit as st

st.set_page_config(
    page_title="AiPERF Copilot",
    layout="wide"
)

st.title("AiPERF Copilot")

question = st.text_input(
    "Ask AiPERF",
    placeholder="Can I release this build?"
)

if st.button("Ask"):

    st.subheader("AI Response")

    st.write("""
Release Risk: Medium

Root Cause:
Gateway CPU utilization increased.

Recommendation:
Review Gateway changes before release.
""")
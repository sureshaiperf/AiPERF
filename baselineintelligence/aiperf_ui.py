# =====================================================
# AiPERF Copilot
# =====================================================

import streamlit as st
from influxdb import InfluxDBClient
from ai_release_advisor import generate_ai_advice

# =====================================================
# PAGE CONFIG
# =====================================================

st.set_page_config(
    page_title="AiPERF Copilot",
    page_icon="🚀",
    layout="wide"
)

# =====================================================
# INFLUXDB
# =====================================================

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

# =====================================================
# UI HEADER
# =====================================================

st.title("AiPERF Copilot")

st.caption(
    "AI-Native Performance Engineering Intelligence Platform"
)

# =====================================================
# SIDEBAR
# =====================================================

with st.sidebar:

    st.header("AiPERF")

    st.write("✅ Transaction Comparison")
    st.write("✅ Service Comparison")
    st.write("✅ Variance Ranking")
    st.write("✅ RCA Engine")
    st.write("✅ GPT Release Advisor")

    st.divider()

    st.write("TechCon 2026 Demo")

# =====================================================
# GET LATEST RUN
# =====================================================

latest_run_id = "UNKNOWN"

try:

    query = """
    SELECT *
    FROM aiperf_ai_insights
    WHERE insight_type='rca'
    ORDER BY time DESC
    LIMIT 1
    """

    rows = list(
        client.query(query).get_points()
    )

    if rows:

        latest_run_id = rows[0].get(
            "run_id",
            "UNKNOWN"
        )

except Exception:
    pass

# =====================================================
# QUICK QUESTIONS
# =====================================================

quick_questions = [

    "Can I release this build?",

    "Summarize this execution",

    "What are the top performance risks?",

    "Which APIs regressed most?",

    "Which service is causing the bottleneck?",

    "Why did response times increase?",

    "Compare current run with baseline",

    "What should I investigate first?",

    "Provide executive summary"

]

selected_question = st.selectbox(
    "Quick Questions",
    quick_questions
)

# =====================================================
# USER QUESTION
# =====================================================

question = st.text_input(
    "Ask AiPERF",
    value=selected_question
)

# =====================================================
# ASK BUTTON
# =====================================================

if st.button("🚀 Ask"):

    try:

        if not question.strip():

            st.warning(
                "Please enter a question."
            )

        else:

            with st.spinner(
                "AiPERF is analyzing execution data..."
            ):

                response_text = generate_ai_advice(
                    user_question=question
                )

                st.session_state[
                    "gpt_response"
                ] = response_text

                st.session_state[
                    "run_id"
                ] = latest_run_id

    except Exception as ex:

        st.error(
            f"Error: {str(ex)}"
        )

# =====================================================
# DISPLAY RESPONSE
# =====================================================

if "gpt_response" in st.session_state:

    run_id = st.session_state.get(
        "run_id",
        latest_run_id
    )

    st.success(
        f"Latest Run : {run_id}"
    )

    st.markdown("---")

    st.subheader(
        "🤖 AiPERF Analysis"
    )

    st.markdown(
        st.session_state[
            "gpt_response"
        ]
    )

else:

    st.info(
        "Ask a question to AiPERF Copilot."
    )
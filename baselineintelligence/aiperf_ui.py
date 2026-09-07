"""Streamlit dashboard for the AiPERF findings and Copilot workflow."""

import streamlit as st
from influxdb import InfluxDBClient

from ai_release_advisor import generate_ai_advice


st.set_page_config(
    page_title="AiPERF Copilot",
    page_icon="🚀",
    layout="wide",
)


@st.cache_resource
def get_client():
    return InfluxDBClient(host="localhost", port=8086, database="jmeter")


client = get_client()


def latest_findings():
    query = """
    SELECT *
    FROM aiperf_findings_package
    ORDER BY time DESC
    LIMIT 1
    """
    rows = list(client.query(query).get_points())
    return rows[0] if rows else {}


def select_quick_question():
    st.session_state["question_input"] = st.session_state["quick_question"]


findings = {}
findings_error = None
try:
    findings = latest_findings()
except Exception as exc:
    findings_error = str(exc)

latest_run_id = findings.get("run_id", "Unavailable")

st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; }
    .hero { padding: 1.4rem 1.6rem; border-radius: 12px;
            background: linear-gradient(120deg,#17365d,#2563eb); color: white; }
    .hero h1 { margin: 0; }
    .hero p { margin: .4rem 0 0; color: #dbeafe; }
    .card { border: 1px solid #dbe3ef; border-radius: 10px;
            padding: 1rem; background: #ffffff; min-height: 92px; }
    .label { color: #64748b; font-size: .82rem; }
    .value { color: #17365d; font-size: 1.25rem; font-weight: 700; }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("AiPERF")
    st.caption("Performance intelligence workflow")
    for feature in (
        "Transaction comparison",
        "Service health",
        "Variance ranking",
        "Anomaly detection",
        "Bottleneck and RCA",
        "Release readiness",
        "Similar execution",
        "GPT Copilot",
    ):
        st.write(f"✅ {feature}")
    st.divider()
    st.caption("Evidence source: InfluxDB findings package")

st.markdown(
    f"""
    <div class="hero">
      <h1>AiPERF Copilot</h1>
      <p>AI-native performance engineering intelligence for the selected execution.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if findings_error:
    st.error(f"Unable to load the latest AiPERF findings package: {findings_error}")
elif not findings:
    st.warning("No AiPERF findings package is available yet. Run the Jenkins pipeline first.")

st.subheader("Current execution")
run_columns = st.columns(4)
run_columns[0].markdown(
    f'<div class="card"><div class="label">Run ID</div><div class="value">{latest_run_id}</div></div>',
    unsafe_allow_html=True,
)
release_impact = findings.get("release_impact", {})
risk = findings.get("risk", {})
run_columns[1].markdown(
    f'<div class="card"><div class="label">Release decision</div><div class="value">{release_impact.get("decision", "Unavailable")}</div></div>',
    unsafe_allow_html=True,
)
run_columns[2].markdown(
    f'<div class="card"><div class="label">Risk level</div><div class="value">{risk.get("level", "Unavailable")}</div></div>',
    unsafe_allow_html=True,
)
run_columns[3].markdown(
    f'<div class="card"><div class="label">Anomaly status</div><div class="value">{findings.get("anomaly_summary", {}).get("status", "Unavailable")}</div></div>',
    unsafe_allow_html=True,
)

st.divider()

st.subheader("Ask AiPERF about this execution")
quick_questions = [
    "Can I release this build?",
    "Summarize this execution",
    "What are the top performance risks?",
    "Which APIs regressed most?",
    "Which service is causing the bottleneck?",
    "Why did response times increase?",
    "Compare the current run with its reference run",
    "What should I investigate first?",
]
st.selectbox(
    "Quick question",
    quick_questions,
    key="quick_question",
    on_change=select_quick_question,
)

if "question_input" not in st.session_state:
    st.session_state["question_input"] = quick_questions[0]

with st.form("aiperf_question_form", clear_on_submit=False):
    st.text_area(
        "Your question",
        key="question_input",
        height=90,
        help="The question is answered using only the selected run's AiPERF findings.",
    )
    ask = st.form_submit_button("🚀 Analyze execution", type="primary")

if ask:
    question = st.session_state["question_input"].strip()
    if not question:
        st.warning("Enter a question before analyzing.")
    elif latest_run_id == "Unavailable":
        st.error("No run-scoped findings are available. Run Jenkins before asking AiPERF.")
    else:
        try:
            with st.spinner(f"Analyzing {latest_run_id} with AiPERF evidence..."):
                response_text = generate_ai_advice(
                    user_question=question,
                    requested_run_id=latest_run_id,
                )
            if not response_text or not response_text.strip():
                raise RuntimeError("AiPERF returned an empty response.")
            st.session_state["gpt_response"] = response_text
            st.session_state["answered_question"] = question
            st.session_state["answered_run_id"] = latest_run_id
        except Exception as exc:
            st.error(f"AiPERF analysis failed: {exc}")

if "gpt_response" in st.session_state:
    st.divider()
    st.subheader("🤖 AiPERF analysis")
    st.caption(
        f"Question: {st.session_state.get('answered_question', '')} | "
        f"Evidence run: {st.session_state.get('answered_run_id', latest_run_id)}"
    )
    st.markdown(st.session_state["gpt_response"])
else:
    st.info("Choose a quick question or enter your own question, then select Analyze execution.")

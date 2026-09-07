"""Streamlit dashboard for the AiPERF findings and Copilot workflow."""

import json
from datetime import datetime, timezone
import streamlit as st
from influxdb import InfluxDBClient

from ai_release_advisor import generate_ai_advice
from release_gate import parse_findings_package


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


def execution_timeline(run_id):
    query = (
        'SELECT * FROM "aiperf_execution_history" '
        f"WHERE run_id='{run_id}' ORDER BY time DESC LIMIT 1"
    )
    rows = list(client.query(query).get_points())
    return rows[0] if rows else {}


def format_epoch(value):
    try:
        epoch = float(value)
        if epoch < 10_000_000_000:
            epoch *= 1000
        return datetime.fromtimestamp(
            epoch / 1000,
            tz=timezone.utc,
        ).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except (TypeError, ValueError, OSError, OverflowError):
        return "Unavailable"


findings = {}
findings_error = None
try:
    findings = latest_findings()
except Exception as exc:
    findings_error = str(exc)

latest_run_id = findings.get("run_id", "Unavailable")
if findings.get("findings_json"):
    try:
        package = parse_findings_package(findings["findings_json"])
        package["run_id"] = findings.get("run_id", package.get("run_id"))
        findings = package
    except (TypeError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        findings_error = f"Findings package JSON is invalid: {exc}"

timeline = {}
if latest_run_id != "Unavailable":
    try:
        timeline = execution_timeline(latest_run_id)
    except Exception as exc:
        findings_error = findings_error or f"Execution timeline unavailable: {exc}"

st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; }
    .hero { padding: 1.4rem 1.6rem; border-radius: 12px;
            background: linear-gradient(120deg,#17365d,#2563eb); color: white; }
    .hero h1 { margin: 0; color: #ffffff; }
    .hero p { margin: .4rem 0 0; color: #dbeafe; }
    .card { border: 1px solid #dbe3ef; border-radius: 10px;
            padding: 1rem; background: #ffffff; min-height: 92px; }
    .label { color: #64748b; font-size: .82rem; }
    .value { color: #17365d; font-size: 1.25rem; font-weight: 700; }
    .timeline-card { border: 1px solid #dbe3ef; border-radius: 10px;
                     padding: .75rem 1rem; background: #f8fafc; }
    .timeline-value { color: #17365d; font-size: .98rem; font-weight: 600;
                      white-space: nowrap; }
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
    st.caption("Evidence flow")
    st.caption("1. InfluxDB run metrics")
    st.caption("2. AiPERF comparisons, anomalies, RCA, and readiness")
    st.caption("3. Findings package")
    st.caption("4. AI explanation and recommendations")

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
timeline_columns = st.columns(3)
timeline_values = (
    ("Start time", format_epoch(timeline.get("run_start_epoch"))),
    ("End time", format_epoch(timeline.get("run_end_epoch"))),
    (
        "Duration",
        f'{float(timeline["duration_seconds"]):,.3f} s'
        if timeline.get("duration_seconds") is not None
        else "Unavailable",
    ),
)
for column, (label, value) in zip(timeline_columns, timeline_values):
    column.markdown(
        f'<div class="timeline-card"><div class="label">{label}</div>'
        f'<div class="timeline-value">{value}</div></div>',
        unsafe_allow_html=True,
    )

st.divider()

st.subheader("Ask AiPERF Copilot")
st.caption(
    "Ask about performance evidence, release readiness, regressions, risks, "
    "root causes, anomalies, services, or recommended next actions."
)
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


def is_aiperf_question(text):
    """Keep execution evidence visible only for AiPERF questions."""
    performance_terms = (
        "release", "readiness", "performance", "api", "latency",
        "throughput", "response", "p95", "p99", "baseline", "risk",
        "service", "bottleneck", "execution", "regression", "transaction",
        "memory", "cpu", "thread", "jvm", "anomaly", "capacity", "sla",
        "availability", "scalability", "reliability", "summary", "compare",
        "executive", "explain", "what happened", "investigate", "next step",
        "run id", "runid", "comparison report", "last run", "date",
    )
    normalized = text.strip().lower()
    if (
        "performance testing" in normalized
        and any(
            phrase in normalized
            for phrase in ("explain", "what is", "what are", "how does", "define")
        )
        and not any(
            term in normalized
            for term in ("run", "execution", "result", "metric", "latency", "p95", "p99")
        )
    ):
        return False
    return any(term in normalized for term in performance_terms)


def is_catalog_question(text):
    normalized = text.strip().lower()
    return (
        ("run" in normalized and ("id" in normalized or "runs" in normalized))
        or "comparison report" in normalized
    )


def ask_copilot(question, previous_question=None, previous_response=None):
    """Send a question with optional conversation context to AiPERF."""
    if previous_question and previous_response:
        question = (
            "Conversation context:\n"
            f"Previous user question: {previous_question}\n"
            f"Previous AiPERF response: {previous_response}\n\n"
            f"Follow-up user question: {question}"
        )
    with st.spinner("AiPERF is preparing your answer..."):
        response_text = generate_ai_advice(
            user_question=question,
            requested_run_id=latest_run_id,
        )
    if not response_text or not response_text.strip():
        raise RuntimeError("AiPERF returned an empty response.")
    return response_text


selected_quick_question = st.selectbox(
    "Quick question",
    quick_questions,
    key="quick_question",
)

if "question_input" not in st.session_state:
    st.session_state["question_input"] = ""

if st.button("Use selected quick question"):
    st.session_state["question_input"] = selected_quick_question
    st.rerun()

with st.form("aiperf_question_form", clear_on_submit=True):
    st.text_area(
        "Your question (independent from the quick-question list)",
        key="question_input",
        height=90,
        help="The question is answered using only the selected run's AiPERF findings.",
    )
    ask = st.form_submit_button("Ask Copilot", type="primary")

if ask:
    question = st.session_state["question_input"].strip()
    if not question:
        st.warning("Enter a question before analyzing.")
    elif latest_run_id == "Unavailable" and not is_catalog_question(question):
        st.error("No run-scoped findings are available. Run Jenkins before asking AiPERF.")
    else:
        try:
            response_text = ask_copilot(question)
            st.session_state["gpt_response"] = response_text
            st.session_state["answered_question"] = question
            st.session_state["answered_run_id"] = latest_run_id
            st.session_state["show_evidence"] = is_aiperf_question(question)
        except Exception as exc:
            st.error(f"AiPERF analysis failed: {exc}")

if "gpt_response" in st.session_state:
    st.divider()
    st.subheader("🤖 AiPERF Says")
    st.caption(f"Question: {st.session_state.get('answered_question', '')}")
    if st.session_state.get("show_evidence", True):
        st.caption(
            f"Evidence run: "
            f"{st.session_state.get('answered_run_id', latest_run_id)}"
        )
    st.markdown(st.session_state["gpt_response"])
    with st.form("aiperf_follow_up_form", clear_on_submit=True):
        st.text_area(
            "Continue the conversation",
            key="follow_up_input",
            height=80,
            placeholder="Reply to AiPERF or ask a follow-up question...",
        )
        follow_up = st.form_submit_button("Ask follow-up", type="primary")
    if follow_up:
        follow_up_question = st.session_state["follow_up_input"].strip()
        if not follow_up_question:
            st.warning("Enter a follow-up question.")
        else:
            try:
                response_text = ask_copilot(
                    follow_up_question,
                    previous_question=st.session_state.get("answered_question"),
                    previous_response=st.session_state.get("gpt_response"),
                )
                st.session_state["gpt_response"] = response_text
                st.session_state["answered_question"] = follow_up_question
                st.session_state["show_evidence"] = is_aiperf_question(
                    follow_up_question
                )
                st.rerun()
            except Exception as exc:
                st.error(f"AiPERF follow-up failed: {exc}")
else:
    st.info("Choose a quick question or enter your own question, then select Ask Copilot.")

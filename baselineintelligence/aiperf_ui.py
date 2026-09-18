"""AiPERF Streamlit Command Center V2.2.

Copilot-first, evidence-grounded UI for InfluxDB OSS 1.8.x.
The persisted AiPERF Findings Package is authoritative for release governance.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import pandas as pd
import streamlit as st
from influxdb import InfluxDBClient

from ai_release_advisor import generate_ai_advice
from release_gate import parse_findings_package

st.set_page_config(
    page_title="AiPERF Copilot Command Center",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

INFLUX_HOST = os.getenv("INFLUX_HOST", "localhost")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DATABASE = os.getenv(
    "INFLUX_DATABASE", os.getenv("INFLUX_DB", "jmeter")
)
INFLUX_USER = os.getenv("INFLUX_USER") or None
INFLUX_PASSWORD = os.getenv("INFLUX_PASSWORD") or None
INFLUX_TIMEOUT_SECONDS = int(os.getenv("INFLUX_TIMEOUT_SECONDS", "30"))
BASELINE_ID = os.getenv("AIPERF_BASELINE_ID", "AIPERF_LOCAL_API_V1")
MAX_RUNS = max(10, int(os.getenv("AIPERF_UI_MAX_RUNS", "100")))
MAX_CHAT_MESSAGES = max(
    2, int(os.getenv("AIPERF_UI_MAX_CHAT_MESSAGES", "12"))
)
MAX_CONTEXT_CHARS = max(
    2000, int(os.getenv("AIPERF_UI_MAX_CONTEXT_CHARS", "12000"))
)
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]+")

PAGES = (
    "AiPERF Copilot",
    "Overview",
    "Performance",
    "Regressions & Improvements",
    "Anomaly Intelligence",
    "Correlation & RCA",
    "Similar Executions",
    "Governance",
)


def text(value: Any, default: str = "") -> str:
    return default if value is None else str(value).strip()


def number(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def integer(value: Any, default: int = 0) -> int:
    return int(number(value, float(default)))


def safe_run_id(value: Any) -> str:
    selected = text(value)
    if not selected or not RUN_ID_PATTERN.fullmatch(selected):
        raise ValueError("RUN_ID is missing or contains unsupported characters")
    return selected


def influx_escape(value: Any) -> str:
    return text(value).replace("\\", "\\\\").replace("'", "\\'")


def bool_value(value: Any) -> bool:
    return value in (1, True, "1", "true", "True", "TRUE")


@st.cache_resource
def get_client() -> InfluxDBClient:
    connection = InfluxDBClient(
        host=INFLUX_HOST,
        port=INFLUX_PORT,
        username=INFLUX_USER,
        password=INFLUX_PASSWORD,
        database=INFLUX_DATABASE,
        timeout=INFLUX_TIMEOUT_SECONDS,
    )
    connection.ping()
    return connection


def query_points(influxql: str) -> list[dict[str, Any]]:
    result = get_client().query(influxql)
    return [dict(point) for point in result.get_points()]


@st.cache_data(ttl=30, show_spinner=False)
def list_runs() -> list[str]:
    """Return unique Findings Package RUN_IDs, newest first.

    InfluxDB OSS 1.8 requires a field in SELECT. findings_json is selected and
    RUN_ID is read first from the tag, then from the JSON contract as fallback.
    """
    rows = query_points(
        'SELECT "findings_json" FROM "aiperf_findings_package" '
        f"ORDER BY time DESC LIMIT {MAX_RUNS}"
    )
    output: list[str] = []
    seen: set[str] = set()
    for row in rows:
        candidate = text(row.get("run_id"))
        if not candidate and row.get("findings_json"):
            try:
                package = json.loads(str(row["findings_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                package = {}
            if isinstance(package, dict):
                candidate = text(package.get("run_id"))
        if not candidate:
            continue
        try:
            candidate = safe_run_id(candidate)
        except ValueError:
            continue
        if candidate not in seen:
            seen.add(candidate)
            output.append(candidate)
    return output


@st.cache_data(ttl=30, show_spinner=False)
def load_findings(selected_run: str) -> dict[str, Any]:
    run = safe_run_id(selected_run)
    rows = query_points(
        'SELECT * FROM "aiperf_findings_package" '
        f'WHERE "run_id"=\'{influx_escape(run)}\' '
        "ORDER BY time DESC LIMIT 1"
    )
    if not rows:
        return {}
    point = rows[0]
    payload = point.get("findings_json")
    if not payload:
        return point
    package = parse_findings_package(payload)
    if not isinstance(package, dict):
        raise TypeError("Findings Package root must be a JSON object")
    package["run_id"] = run
    package["_findings_point_time"] = point.get("time")
    return package


@st.cache_data(ttl=30, show_spinner=False)
def load_latest_point(measurement: str, selected_run: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_]+", measurement):
        raise ValueError("Unsupported measurement name")
    run = safe_run_id(selected_run)
    rows = query_points(
        f'SELECT * FROM "{measurement}" '
        f'WHERE "run_id"=\'{influx_escape(run)}\' '
        "ORDER BY time DESC LIMIT 1"
    )
    return rows[0] if rows else {}


@st.cache_data(ttl=30, show_spinner=False)
def load_registry() -> dict[str, Any]:
    rows = query_points(
        'SELECT * FROM "aiperf_baseline_registry" '
        f'WHERE "baseline_id"=\'{influx_escape(BASELINE_ID)}\' '
        "ORDER BY time DESC LIMIT 1"
    )
    return rows[0] if rows else {}


def refresh_all() -> None:
    list_runs.clear()
    load_findings.clear()
    load_latest_point.clear()
    load_registry.clear()
    st.rerun()


def format_time(value: Any) -> str:
    if value is None:
        return "Unavailable"
    try:
        raw = text(value)
        if isinstance(value, (int, float)) or raw.replace(".", "", 1).isdigit():
            epoch = float(value)
            if epoch < 10_000_000_000:
                epoch *= 1000
            result = datetime.fromtimestamp(epoch / 1000, tz=timezone.utc)
        else:
            result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return result.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except (TypeError, ValueError, OSError, OverflowError):
        return text(value, "Unavailable")


def make_frame(
    rows: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    columns: list[str] | None = None,
) -> pd.DataFrame:
    source = [rows] if isinstance(rows, Mapping) else list(rows)
    result = pd.DataFrame([dict(item) for item in source])
    if columns:
        for column in columns:
            if column not in result.columns:
                result[column] = None
        result = result[columns]
    return result


def status_tone(value: Any) -> str:
    normalized = text(value).upper()
    if normalized in {
        "PROCEED", "APPROVED", "NORMAL", "PRODUCTION READY",
        "HEALTHY", "IMPROVEMENT", "COMPLETE",
    }:
        return "good"
    if normalized in {
        "BLOCK", "CRITICAL", "HIGH", "NOT READY", "FAILED",
    }:
        return "bad"
    if normalized in {
        "CONDITIONAL", "WARNING", "MEDIUM", "READY WITH OBSERVATIONS",
        "PARTIAL",
    }:
        return "warn"
    return "neutral"


def metric_card(column: Any, label: str, value: Any, status: Any = "") -> None:
    css_class = status_tone(status)
    column.markdown(
        '<div class="metric-card '
        + css_class
        + '"><div class="metric-label">'
        + html.escape(label)
        + '</div><div class="metric-value">'
        + html.escape(text(value, "Unavailable"))
        + "</div></div>",
        unsafe_allow_html=True,
    )


def chat_context(messages: list[dict[str, str]]) -> str:
    rendered = "\n\n".join(
        f"{message['role'].upper()}: {message['content']}"
        for message in messages[-MAX_CHAT_MESSAGES:]
    )
    return rendered[-MAX_CONTEXT_CHARS:]


def invoke_copilot(
    selected_run: str,
    question: str,
    prior_messages: list[dict[str, str]],
) -> str:
    context = chat_context(prior_messages)
    grounded_question = (
        question
        if not context
        else f"Conversation context:\n{context}\n\nUSER: {question}"
    )
    response = generate_ai_advice(
        user_question=grounded_question,
        requested_run_id=selected_run,
    )
    if not response or not response.strip():
        raise RuntimeError("AiPERF GPT returned an empty response")
    return response.strip()


st.markdown(
    """
<style>
.block-container {padding-top:1.3rem; padding-bottom:3rem; max-width:1600px;}
.hero {padding:1.45rem 1.75rem; border-radius:16px; color:#fff;
       background:linear-gradient(120deg,#17365d,#2563eb); margin-bottom:1rem;}
.hero h1 {color:#fff; margin:0; font-size:2.15rem;}
.hero p {color:#dbeafe; margin:.4rem 0 0;}
.metric-card {border:1px solid #dbe3ef; border-left:5px solid #2563eb;
              border-radius:12px; padding:.9rem 1rem; background:#fff; min-height:92px;}
.metric-card.good {border-left-color:#16a34a;}
.metric-card.warn {border-left-color:#d97706;}
.metric-card.bad {border-left-color:#dc2626;}
.metric-label {color:#64748b; font-size:.75rem; text-transform:uppercase; letter-spacing:.04em;}
.metric-value {color:#17365d; font-size:1.12rem; font-weight:750; margin-top:.3rem; overflow-wrap:anywhere;}
</style>
""",
    unsafe_allow_html=True,
)

try:
    get_client()
    runs = list_runs()
except Exception as exc:
    st.error(
        f"InfluxDB connection or run discovery failed: "
        f"{type(exc).__name__}: {exc}"
    )
    st.stop()

if not runs:
    st.error(
        "No AiPERF Findings Package runs were returned from "
        f"{INFLUX_HOST}:{INFLUX_PORT}/{INFLUX_DATABASE}."
    )
    st.code(
        'SELECT "findings_json" FROM "aiperf_findings_package" '
        "ORDER BY time DESC LIMIT 5",
        language="sql",
    )
    st.stop()

with st.sidebar:
    st.title("AiPERF")
    st.caption("Performance Engineering Command Center")
    st.success(
        f"InfluxDB connected\n{INFLUX_HOST}:{INFLUX_PORT}/{INFLUX_DATABASE}"
    )
    if st.button("Refresh data", use_container_width=True):
        refresh_all()

    if st.session_state.get("selected_run_id") not in runs:
        st.session_state["selected_run_id"] = runs[0]
    selected_run = st.selectbox(
        "Execution RUN_ID",
        runs,
        key="selected_run_id",
    )
    selected_run = safe_run_id(st.session_state["selected_run_id"])

    st.divider()
    st.subheader("Command Center")
    selected_page = st.radio(
        "Navigation",
        PAGES,
        index=0,
        key="selected_page_v2_2",
        label_visibility="collapsed",
    )
    if selected_page == "AiPERF Copilot":
        st.success("GPT Copilot ready")
        st.caption(f"Evidence RUN_ID: {selected_run}")

    registry = load_registry()
    st.divider()
    st.caption("Approved baseline")
    st.write(text(registry.get("representative_run_id"), "Unavailable"))
    st.caption(f"Registry: {text(registry.get('status'), 'Unavailable')}")
    st.caption(
        f"Semantic version: {text(registry.get('semantic_version'), 'Unavailable')}"
    )

try:
    findings = load_findings(selected_run)
    execution = load_latest_point("aiperf_execution_history", selected_run)
    readiness = load_latest_point("aiperf_release_readiness", selected_run)
except Exception as exc:
    st.error(f"Unable to load {selected_run}: {type(exc).__name__}: {exc}")
    st.stop()

if not findings:
    st.error(f"No Findings Package exists for {selected_run}")
    st.stop()

release = findings.get("release_impact") or findings.get("release_decision") or {}
risk = findings.get("risk") or {}
anomaly = findings.get("anomaly_summary") or {}
baseline = findings.get("baseline") or {}
confidence = findings.get("confidence") or {}
quality = findings.get("data_quality") or {}

st.markdown(
    '<div class="hero"><h1>AiPERF Copilot Command Center</h1>'
    '<p>Evidence-first performance intelligence, approved-baseline governance, '
    'and GPT-guided release analysis.</p></div>',
    unsafe_allow_html=True,
)
st.caption(
    f"Selected evidence RUN_ID: {selected_run} | Findings generated: "
    f"{format_time(findings.get('generated_time') or findings.get('_findings_point_time'))}"
)

cards = st.columns(6)
metric_card(cards[0], "Release decision", release.get("decision"), release.get("decision"))
metric_card(
    cards[1],
    "Risk",
    f"{text(risk.get('level'), 'Unavailable')} ({integer(risk.get('score'))})",
    risk.get("level"),
)
metric_card(
    cards[2],
    "Readiness",
    f"{integer(readiness.get('release_score'))}/100",
    readiness.get("status"),
)
metric_card(cards[3], "Baseline", baseline.get("status"), baseline.get("status"))
metric_card(cards[4], "Anomaly", anomaly.get("status"), anomaly.get("status"))
metric_card(
    cards[5],
    "Confidence",
    f"{text(confidence.get('level'), 'Unavailable')} "
    f"({number(confidence.get('overall')):.3f})",
    confidence.get("level"),
)

if selected_page == "AiPERF Copilot":
    st.header("🤖 AiPERF Copilot")
    st.success("GPT integration is available")
    st.info(
        f"Evidence RUN_ID: {selected_run}\n\n"
        f"Approved baseline: "
        f"{text(baseline.get('approved_baseline_run_id'), 'Unavailable')}\n\n"
        f"Authoritative release decision: "
        f"{text(release.get('decision'), 'Unavailable')}"
    )
    st.caption(
        "Copilot uses ai_release_advisor.generate_ai_advice and is grounded "
        "in the selected run's deterministic Findings Package."
    )

    suggested_questions = (
        "Can I release this build?",
        "Summarize this execution for leadership.",
        "Explain the approved baseline comparison.",
        "What are the actionable regressions?",
        "Explain degrading anomalies and beneficial outliers.",
        "What evidence supports the release decision?",
        "What evidence is missing for confirmed RCA?",
        "What should I investigate next?",
    )
    suggestion = st.selectbox("Suggested question", suggested_questions)
    action_columns = st.columns(2)
    ask_suggestion = action_columns[0].button(
        "Ask selected question",
        use_container_width=True,
        type="primary",
    )
    clear_chat = action_columns[1].button(
        "Clear conversation",
        use_container_width=True,
    )

    if "chat_by_run" not in st.session_state:
        st.session_state["chat_by_run"] = {}
    conversations = st.session_state["chat_by_run"]
    messages = conversations.setdefault(selected_run, [])

    if clear_chat:
        conversations[selected_run] = []
        st.rerun()

    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Ask AiPERF Copilot about this execution")
    if ask_suggestion:
        prompt = suggestion

    if prompt:
        messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        try:
            with st.chat_message("assistant"):
                with st.spinner("AiPERF GPT is analyzing structured evidence..."):
                    response = invoke_copilot(selected_run, prompt, messages[:-1])
                st.markdown(response)
            messages.append({"role": "assistant", "content": response})
            conversations[selected_run] = messages[-MAX_CHAT_MESSAGES:]
        except Exception as exc:
            st.error(f"AiPERF Copilot failed: {type(exc).__name__}: {exc}")

elif selected_page == "Overview":
    st.header("Executive overview")
    metrics = st.columns(6)
    metric_card(metrics[0], "Avg RT", f"{number(execution.get('avg_rt')):.3f} ms")
    metric_card(metrics[1], "P95", f"{number(execution.get('p95')):.2f} ms")
    metric_card(metrics[2], "P99", f"{number(execution.get('p99')):.2f} ms")
    metric_card(
        metrics[3],
        "Throughput",
        f"{number(execution.get('throughput_rps', execution.get('throughput'))):.3f} RPS",
    )
    metric_card(metrics[4], "Error rate", f"{number(execution.get('error_rate')):.3f}%")
    metric_card(metrics[5], "Samples", f"{integer(execution.get('sample_count')):,}")
    st.subheader("Executive summary")
    st.write(text(findings.get("executive_summary"), "No summary available"))
    st.subheader("Recommended actions")
    actions = findings.get("recommended_actions") or []
    if actions:
        for action in actions:
            st.markdown(f"- {text(action)}")
    else:
        st.info("No recommendations recorded")

elif selected_page == "Performance":
    st.header("Performance evidence")
    execution_columns = [
        "avg_rt", "p90", "p95", "p99", "throughput", "throughput_rps",
        "error_rate", "sample_count", "test_duration_seconds", "duration_source",
    ]
    st.dataframe(
        make_frame(execution, execution_columns),
        use_container_width=True,
        hide_index=True,
    )
    st.subheader("Service comparison")
    service_rows = findings.get("service_impacts") or []
    st.dataframe(make_frame(service_rows), use_container_width=True, hide_index=True)
    downstream = [
        row for row in service_rows
        if text(row.get("service_name")).lower() != "gateway"
        and bool_value(row.get("request_count_comparable"))
    ]
    downstream_count = sum(integer(row.get("request_count_current")) for row in downstream)
    sample_count = integer(execution.get("sample_count"))
    difference = downstream_count - sample_count
    st.metric(
        "JMeter to downstream reconciliation",
        "PASSED" if sample_count > 0 and difference == 0 else "CHECK REQUIRED",
        delta=f"Difference: {difference:,}",
    )

elif selected_page == "Regressions & Improvements":
    left, right = st.columns(2)
    with left:
        st.header("Actionable regressions")
        regressions = findings.get("top_regressions") or []
        if regressions:
            st.dataframe(make_frame(regressions), use_container_width=True, hide_index=True)
        else:
            st.success("No actionable regressions")
    with right:
        st.header("Top improvements")
        improvements = findings.get("top_improvements") or []
        if improvements:
            st.dataframe(make_frame(improvements), use_container_width=True, hide_index=True)
        else:
            st.info("No improvements recorded")
    st.subheader("Transaction comparison")
    st.dataframe(
        make_frame(findings.get("transaction_impacts") or []),
        use_container_width=True,
        hide_index=True,
    )

elif selected_page == "Anomaly Intelligence":
    st.header("Direction-aware anomaly intelligence")
    anomaly_cards = st.columns(4)
    anomaly_cards[0].metric("Evaluated metrics", integer(anomaly.get("total_metrics")))
    anomaly_cards[1].metric("Degrading anomalies", integer(anomaly.get("anomaly_count")))
    anomaly_cards[2].metric("Critical anomalies", integer(anomaly.get("critical_count")))
    anomaly_cards[3].metric(
        "Beneficial outliers",
        integer(anomaly.get("beneficial_outlier_count")),
    )
    anomaly_columns = [
        "metric", "status", "severity", "direction", "value", "baseline",
        "deviation_pct", "anomaly_score", "statistical_outlier",
        "release_risk", "is_anomaly", "sample_count",
        "incompatible_sample_count", "semantic_compatibility",
    ]
    st.dataframe(
        make_frame(findings.get("anomalies") or [], anomaly_columns),
        use_container_width=True,
        hide_index=True,
    )

elif selected_page == "Correlation & RCA":
    st.header("Correlation and root-cause intelligence")
    correlation = findings.get("correlation_summary") or {}
    bottleneck = findings.get("bottleneck_summary") or {}
    summary_cards = st.columns(3)
    metric_card(
        summary_cards[0],
        "Correlation class",
        correlation.get("classification"),
        correlation.get("classification"),
    )
    metric_card(
        summary_cards[1],
        "Causal confidence",
        f"{text(correlation.get('confidence_level'), 'LOW')} "
        f"({number(correlation.get('confidence')):.3f})",
    )
    metric_card(
        summary_cards[2],
        "Primary bottleneck",
        bottleneck.get("primary_bottleneck") or "None",
        "CRITICAL" if bottleneck.get("primary_bottleneck") else "NORMAL",
    )
    st.subheader("Correlation summary")
    st.write(text(correlation.get("summary"), "Unavailable"))
    detail_columns = st.columns(2)
    with detail_columns[0]:
        st.subheader("Correlation details")
        st.json(findings.get("correlation_intelligence") or {}, expanded=False)
    with detail_columns[1]:
        st.subheader("Bottleneck assessment")
        st.json(bottleneck, expanded=True)

elif selected_page == "Similar Executions":
    st.header("Similar execution intelligence")
    similar = findings.get("similar_executions") or []
    if similar:
        st.dataframe(make_frame(similar), use_container_width=True, hide_index=True)
    else:
        st.info("No similar executions are available")
    st.subheader("Outcome-aware historical findings")
    historical = (
        findings.get("similar_historical_findings")
        or findings.get("historical_findings")
        or []
    )
    if historical:
        st.dataframe(make_frame(historical), use_container_width=True, hide_index=True)
    else:
        st.info("No outcome-aware historical findings are available")

elif selected_page == "Governance":
    st.header("Release governance")
    columns = st.columns(2)
    with columns[0]:
        st.subheader("Approved baseline")
        st.json(baseline, expanded=True)
        st.subheader("Release impact")
        st.json(release, expanded=True)
    with columns[1]:
        st.subheader("Evidence quality")
        st.json(quality, expanded=True)
        st.subheader("Component status")
        st.json(findings.get("component_status") or {}, expanded=True)
        st.subheader("Processing warnings")
        warnings = findings.get("processing_warnings") or []
        if warnings:
            for warning in warnings:
                st.warning(text(warning))
        else:
            st.success("No processing warnings")

st.divider()
st.caption(
    "AiPERF Command Center V2.2 | Deterministic evidence is authoritative; "
    "GPT provides explanation and guidance."
)

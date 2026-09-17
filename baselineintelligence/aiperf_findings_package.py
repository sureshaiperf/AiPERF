"""Build the run-scoped AiPERF findings package.

This module is the deterministic context builder between AiPERF intelligence
engines and AI consumers. It collects current-run evidence, executes
bottleneck and correlation intelligence, enriches the package with bounded
historical evidence, calculates explainable data quality and confidence, and
persists one JSON findings contract to InfluxDB.

No GPT call is made from this module.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping, Sequence

try:
    from influxdb import InfluxDBClient
except ImportError:  # pragma: no cover
    InfluxDBClient = None

try:
    from .anomaly_detection import OUTPUT_MEASUREMENT
    from .bottleneck_intelligence import (
        analyze_bottlenecks,
        persist_bottleneck_intelligence,
        query_bottlenecks,
        summarize_bottlenecks,
    )
    from .correlation_intelligence import (
        analyze_correlation,
        persist_correlation_intelligence,
        query_correlations,
        summarize_correlations,
    )
    from .embeddings_knowledge_layer import (
        create_embedding,
        package_to_text,
        store_finding,
    )
    from .evidence_orchestrator import prepare_evidence
    from .historical_findings_search import (
        find_similar_findings,
        summarize_similar_outcomes,
    )
except ImportError:
    from anomaly_detection import OUTPUT_MEASUREMENT
    from bottleneck_intelligence import (
        analyze_bottlenecks,
        persist_bottleneck_intelligence,
        query_bottlenecks,
        summarize_bottlenecks,
    )
    from correlation_intelligence import (
        analyze_correlation,
        persist_correlation_intelligence,
        query_correlations,
        summarize_correlations,
    )
    from embeddings_knowledge_layer import (
        create_embedding,
        package_to_text,
        store_finding,
    )
    from evidence_orchestrator import prepare_evidence
    from historical_findings_search import (
        find_similar_findings,
        summarize_similar_outcomes,
    )

DB_HOST = os.getenv("INFLUX_HOST", "localhost")
DB_PORT = int(os.getenv("INFLUX_PORT", "8086"))
DB_NAME = os.getenv("INFLUX_DATABASE", "jmeter")
MEASUREMENT_NAME = "aiperf_findings_package"
FINDINGS_PACKAGE_VERSION = "2.0"
MAX_TOP_VARIANCES = int(os.getenv("AIPERF_MAX_TOP_VARIANCES", "10"))
MAX_PERSISTED_ITEMS = int(os.getenv("AIPERF_MAX_PERSISTED_ITEMS", "100"))
RESOURCE_PRESSURE_THRESHOLD_PCT = float(
    os.getenv("AIPERF_RESOURCE_PRESSURE_THRESHOLD_PCT", "80.0")
)
RESOURCE_METRICS = {
    "cpu", "cpu_pct", "system_cpu", "process_cpu", "heap", "heap_pct",
    "jvm_memory", "jvm_memory_pct", "memory_pct", "gc", "gc_overhead",
    "gc_pct", "threads", "jvm_threads", "executor_active",
}

client: Any = None


def _number(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _text(value: Any, default: str = "") -> str:
    return default if value is None else str(value).strip()


def _safe_run_id(value: Any) -> str:
    run_id = _text(value)
    if not run_id:
        raise ValueError("RUN_ID is required")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", run_id):
        raise ValueError("RUN_ID contains unsupported characters")
    return run_id


def _query_points(query: str, *, required: bool = False) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in client.query(query).get_points()]
    except Exception:
        if required:
            raise
        return []


def _unique_by_run(rows: Iterable[Mapping[str, Any]], current_run_id: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        row = dict(raw)
        row_run = _text(row.get("run_id") or row.get("historical_run_id"))
        if row_run == current_run_id:
            continue
        key = row_run or json.dumps(row, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            output.append(row)
    return output


def get_latest_run_id(requested_run_id: str | None = None) -> str:
    if requested_run_id:
        return _safe_run_id(requested_run_id)
    rows = _query_points(
        'SELECT * FROM "aiperf_variance_ranking" ORDER BY time DESC LIMIT 1',
        required=True,
    )
    if not rows:
        raise RuntimeError("No records found in aiperf_variance_ranking")
    return _safe_run_id(rows[0].get("run_id"))


def get_top_variances(run_id: str) -> list[dict[str, Any]]:
    rows = _query_points(
        'SELECT * FROM "aiperf_variance_ranking" '
        f"WHERE run_id='{_safe_run_id(run_id)}'"
    )
    rows.sort(key=lambda row: abs(_number(row.get("variance_pct"))), reverse=True)
    return rows[:MAX_TOP_VARIANCES]


def get_transaction_impacts(run_id: str) -> list[dict[str, Any]]:
    return _query_points(
        'SELECT * FROM "aiperf_transaction_comparison" '
        f"WHERE current_run_id='{_safe_run_id(run_id)}'"
    )[:MAX_PERSISTED_ITEMS]


def get_service_impacts(run_id: str) -> list[dict[str, Any]]:
    return _query_points(
        'SELECT * FROM "aiperf_service_comparison" '
        f"WHERE current_run_id='{_safe_run_id(run_id)}'"
    )[:MAX_PERSISTED_ITEMS]


def get_service_health_records(run_id: str) -> list[dict[str, Any]]:
    return _query_points(
        'SELECT * FROM "aiperf_service_health" '
        f"WHERE run_id='{_safe_run_id(run_id)}'"
    )


def get_ai_insights(run_id: str) -> list[dict[str, Any]]:
    return _query_points(
        'SELECT * FROM "aiperf_ai_insights" '
        f"WHERE run_id='{_safe_run_id(run_id)}'"
    )


def get_anomalies(run_id: str) -> list[dict[str, Any]]:
    return _query_points(
        f'SELECT * FROM "{OUTPUT_MEASUREMENT}" '
        f"WHERE run_id='{_safe_run_id(run_id)}'"
    )


def get_observed_release_outcome(run_id: str) -> dict[str, Any] | None:
    """Return the latest observed production outcome for the selected run."""
    rows = _query_points(
        'SELECT * FROM "aiperf_release_outcome" '
        f"WHERE run_id='{_safe_run_id(run_id)}' ORDER BY time DESC LIMIT 1"
    )
    if not rows:
        return None
    row = dict(rows[0])
    outcome = _text(row.get("outcome"), "NOT_OBSERVED").upper()
    return {
        "run_id": _safe_run_id(row.get("run_id") or run_id),
        "release_decision": _text(row.get("release_decision"), "UNKNOWN").upper(),
        "outcome": outcome,
        "production_status": _text(
            row.get("production_status"), "NOT_OBSERVED"
        ).upper(),
        "incident_count": max(0, int(_number(row.get("incident_count")))),
        "rollback_flag": row.get("rollback_flag") in (1, True, "1", "true", "True"),
        "production_p95": _number(row.get("production_p95")),
        "production_error_rate": _number(row.get("production_error_rate")),
        "observation_window_days": max(
            0, int(_number(row.get("observation_window_days")))
        ),
        "observed_at": _text(row.get("observed_at") or row.get("time")),
        "notes": _text(row.get("notes")),
        "source": _text(row.get("source"), "MANUAL"),
        "schema_version": _text(row.get("schema_version"), "1.0"),
        "outcome_observed": outcome not in {"", "UNKNOWN", "NOT_OBSERVED"},
    }


def build_anomaly_summary(anomalies: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    records = [dict(item) for item in anomalies]
    if not records:
        return {
            "status": "NO_DATA",
            "total_metrics": 0,
            "anomaly_count": 0,
            "critical_count": 0,
            "warning_count": 0,
            "metrics": [],
            "message": "No anomaly detection results were found for this run.",
        }
    detected = [
        item for item in records
        if item.get("is_anomaly") in (1, True, "1", "true", "True")
    ]
    critical = sum(_text(item.get("severity")).upper() == "CRITICAL" for item in detected)
    warning = sum(_text(item.get("severity")).upper() == "WARNING" for item in detected)
    status = "CRITICAL" if critical else "WARNING" if warning else "NORMAL"
    metrics = [
        {
            "metric": _text(item.get("metric"), "UNKNOWN"),
            "severity": _text(item.get("severity"), "UNKNOWN"),
            "deviation_pct": round(_number(item.get("deviation_pct")), 3),
            "anomaly_score": round(_number(item.get("anomaly_score")), 3),
        }
        for item in detected
    ]
    message = (
        f"{len(detected)} anomalous metric(s) detected: "
        + ", ".join(f"{item['metric']} ({item['deviation_pct']:.2f}%)" for item in metrics)
        + "."
        if detected else "No anomalous metrics detected."
    )
    return {
        "status": status,
        "total_metrics": len(records),
        "anomaly_count": len(detected),
        "critical_count": critical,
        "warning_count": warning,
        "metrics": metrics,
        "message": message,
    }


def build_summary(top_variances: Iterable[Mapping[str, Any]], anomaly_summary: Mapping[str, Any]) -> str:
    records = [dict(item) for item in top_variances]
    if not records:
        return "No performance variance records were found for the selected run."
    regressions = [item for item in records if _is_actionable_regression(item)]
    improvements = [item for item in records if _number(item.get("variance_pct")) < 0]
    resource_observations = [
        item for item in records
        if _number(item.get("variance_pct")) > 0
        and _text(item.get("entity_type")).lower() == "service"
        and _is_resource_metric(item.get("metric"))
        and not _is_actionable_regression(item)
    ]
    if regressions:
        largest = max(regressions, key=lambda item: _number(item.get("variance_pct")))
        variance = _number(largest.get("variance_pct"))
        summary = (
            f"Analyzed {len(records)} top variance(s): {len(regressions)} actionable "
            f"regression(s), {len(improvements)} improvement(s), and "
            f"{len(resource_observations)} low-utilization resource observation(s). "
            f"The largest actionable regression was "
            f"{_text(largest.get('entity_name'), 'UNKNOWN')} / "
            f"{_text(largest.get('metric'), 'UNKNOWN')} at {variance:.2f}%."
        )
    else:
        largest = min(improvements, key=lambda item: _number(item.get("variance_pct"))) if improvements else None
        summary = (
            f"Analyzed {len(records)} top variance(s): 0 actionable regressions, "
            f"{len(improvements)} improvement(s), and "
            f"{len(resource_observations)} low-utilization resource observation(s)."
        )
        if largest:
            summary += (
                f" The largest improvement was "
                f"{_text(largest.get('entity_name'), 'UNKNOWN')} / "
                f"{_text(largest.get('metric'), 'UNKNOWN')} at "
                f"{_number(largest.get('variance_pct')):.2f}%."
            )
    if anomaly_summary.get("anomaly_count"):
        summary += " " + _text(anomaly_summary.get("message"))
    elif anomaly_summary.get("status") == "NORMAL":
        summary += " No anomalous metrics were detected."
    return summary


def _is_resource_metric(metric: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", _text(metric).lower()).strip("_")
    return normalized in RESOURCE_METRICS


def _is_actionable_regression(item: Mapping[str, Any]) -> bool:
    variance = _number(item.get("variance_pct"))
    if variance <= 0:
        return False
    if _text(item.get("entity_type")).lower() != "service":
        return True
    if not _is_resource_metric(item.get("metric")):
        return True
    current_value = _number(item.get("current_value"))
    return current_value >= RESOURCE_PRESSURE_THRESHOLD_PCT


def get_top_regressions(top_variances: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records = [dict(item) for item in top_variances if _is_actionable_regression(item)]
    records.sort(key=lambda item: _number(item.get("variance_pct")), reverse=True)
    return records[:5]


def get_top_improvements(top_variances: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records = [dict(item) for item in top_variances if _number(item.get("variance_pct")) < 0]
    records.sort(key=lambda item: _number(item.get("variance_pct")))
    return records[:5]


def build_service_health(
    service_impacts: Iterable[Mapping[str, Any]],
    persisted_health: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    health: dict[str, dict[str, Any]] = {}
    for raw in service_impacts:
        row = dict(raw)
        service_name = _text(row.get("service_name"), "UNKNOWN")
        rt_variance = abs(_number(row.get("avg_rt_variance_pct")))
        heap_variance = abs(_number(row.get("heap_pct_variance_pct")))
        gc_variance = abs(_number(row.get("gc_variance_pct")))
        score = max(rt_variance, heap_variance, gc_variance)
        status = "CRITICAL" if score >= 50 else "WARNING" if score >= 20 else "HEALTHY"
        health[service_name] = {
            "status": status,
            "variance_score": round(score, 2),
            "avg_rt_baseline": _number(row.get("avg_rt_baseline")),
            "avg_rt_current": _number(row.get("avg_rt_current")),
            "avg_rt_variance_pct": _number(row.get("avg_rt_variance_pct")),
            "heap_pct_baseline": _number(row.get("heap_pct_baseline")),
            "heap_pct_current": _number(row.get("heap_pct_current")),
            "heap_pct_variance_pct": _number(row.get("heap_pct_variance_pct")),
            "gc_baseline": _number(row.get("gc_baseline")),
            "gc_current": _number(row.get("gc_current")),
            "gc_variance_pct": _number(row.get("gc_variance_pct")),
            "active_requests": _number(row.get("active_requests_current")),
            "executor_active": _number(row.get("executor_active_current")),
            "request_count": _number(row.get("request_count_current")),
            "request_count_comparable": bool(row.get("request_count_comparable", False)),
        }
    for raw in persisted_health:
        row = dict(raw)
        service_name = _text(row.get("service_name"), "UNKNOWN")
        existing = health.setdefault(service_name, {})
        existing.update({
            "status": _text(row.get("status"), existing.get("status", "UNKNOWN")),
            "health_score": _number(row.get("health_score")),
            "reason": _text(row.get("reason")),
            "cpu_pct": _number(row.get("cpu_pct")),
            "system_cpu": _number(row.get("system_cpu", row.get("cpu_pct"))),
            "process_cpu": _number(row.get("process_cpu")),
            "heap_pct": _number(row.get("heap_pct", existing.get("heap_pct_current"))),
            "gc_overhead": _number(row.get("gc_overhead", existing.get("gc_current"))),
            "active_requests": _number(row.get("active_requests", existing.get("active_requests"))),
            "executor_active": _number(row.get("executor_active", existing.get("executor_active"))),
            "jvm_threads": _number(row.get("jvm_threads")),
        })
    return health


def calculate_risk(
    top_regressions: Iterable[Mapping[str, Any]],
    anomaly_summary: Mapping[str, Any],
) -> dict[str, Any]:
    regressions = [dict(item) for item in top_regressions]
    highest_variance = max((abs(_number(item.get("variance_pct"))) for item in regressions), default=0.0)
    variance_score = min(int(highest_variance / 5), 60)
    anomaly_score = min(
        int(anomaly_summary.get("critical_count", 0)) * 30
        + int(anomaly_summary.get("warning_count", 0)) * 10,
        40,
    )
    risk_score = min(variance_score + anomaly_score, 100)
    level = (
        "CRITICAL" if risk_score >= 80 else
        "HIGH" if risk_score >= 60 else
        "MEDIUM" if risk_score >= 30 else
        "LOW"
    )
    return {
        "level": level,
        "score": risk_score,
        "highest_regression_pct": round(highest_variance, 3),
        "variance_score": variance_score,
        "anomaly_contribution": anomaly_score,
        "basis": "Variance and anomaly severity. Business criticality and SLA weighting are not yet enabled.",
    }


def determine_baseline_status(
    run_id: str,
    transaction_impacts: Sequence[Mapping[str, Any]],
    service_impacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    approved_run_id = _text(
        os.getenv("AIPERF_APPROVED_BASELINE_RUN_ID")
    )
    comparison_run_ids = {
        _text(
            item.get("comparison_run_id")
            or item.get("similar_run_id")
        )
        for item in [*transaction_impacts, *service_impacts]
        if _text(
            item.get("comparison_run_id")
            or item.get("similar_run_id")
        )
    }

    if approved_run_id and approved_run_id in comparison_run_ids:
        return {
            "status": "APPROVED",
            "approved_baseline_run_id": approved_run_id,
            "comparison_run_ids": sorted(comparison_run_ids),
            "reason": "The comparison run matches the explicitly approved baseline.",
        }

    if comparison_run_ids:
        return {
            "status": "REFERENCE_ONLY",
            "approved_baseline_run_id": approved_run_id or None,
            "comparison_run_ids": sorted(comparison_run_ids),
            "reason": (
                "Comparison evidence exists, but no matching approved baseline "
                "was configured."
            ),
        }

    return {
        "status": "BASELINE_UNAVAILABLE",
        "approved_baseline_run_id": approved_run_id or None,
        "comparison_run_ids": [],
        "reason": "No comparison or approved baseline evidence exists for this run.",
    }


def calculate_release_impact(
    risk: Mapping[str, Any],
    anomaly_summary: Mapping[str, Any],
    service_health: Mapping[str, Mapping[str, Any]],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    critical_services = sum(
        _text(details.get("status")).upper() == "CRITICAL"
        for details in service_health.values()
    )
    release_blocked = (
        risk.get("level") == "CRITICAL"
        or int(anomaly_summary.get("critical_count", 0)) > 0
        or critical_services > 0
    )
    baseline_status = _text(
        baseline.get("status"), "BASELINE_UNAVAILABLE"
    ).upper()

    if release_blocked:
        decision = "BLOCK"
        rationale = "Critical performance risk requires remediation before release."
        decision_type = "PREDICTED_BLOCKING"
    elif baseline_status != "APPROVED":
        decision = "OBSERVE"
        release_blocked = False
        rationale = (
            f"{baseline.get('reason', 'Approved baseline evidence is unavailable')} "
            "The gate remains non-blocking while controlled evidence is collected."
        )
        decision_type = f"PREDICTED_{baseline_status}"
    elif risk.get("level") in {"HIGH", "MEDIUM"} or int(
        anomaly_summary.get("warning_count", 0)
    ):
        decision = "CONDITIONAL"
        rationale = (
            "Release requires review and documented acceptance of the observed "
            "performance risk against the approved baseline."
        )
        decision_type = "PREDICTED_APPROVED_BASELINE"
    else:
        decision = "PROCEED"
        rationale = (
            "No critical release-blocking performance signals were detected "
            "against the approved baseline."
        )
        decision_type = "PREDICTED_APPROVED_BASELINE"

    return {
        "decision": decision,
        "release_blocked": release_blocked,
        "rationale": rationale,
        "risk_level": risk.get("level", "UNKNOWN"),
        "anomaly_status": anomaly_summary.get("status", "UNKNOWN"),
        "critical_service_count": critical_services,
        "decision_type": decision_type,
        "baseline_status": baseline_status,
        "approved_baseline_run_id": baseline.get("approved_baseline_run_id"),
        "comparison_run_ids": list(baseline.get("comparison_run_ids") or []),
    }


def generate_recommendations(
    top_regressions: Iterable[Mapping[str, Any]],
    service_health: Mapping[str, Mapping[str, Any]],
    anomaly_summary: Mapping[str, Any],
    correlation_summary: Mapping[str, Any],
) -> list[str]:
    recommendations: list[str] = []
    primary = correlation_summary.get("primary_transaction") or {}
    classification = _text(correlation_summary.get("classification"))
    if primary:
        recommendations.append(
            f"Prioritize {_text(primary.get('entity'), 'the primary transaction')} "
            f"{_text(primary.get('metric'), 'latency')} regression "
            f"({abs(_number(primary.get('variance_pct'))):.2f}%)."
        )
    if classification == "ISOLATED_TAIL_LATENCY":
        recommendations.append(
            "Compare slow-request traces and downstream timings for the affected path; current evidence indicates tail-latency variation rather than broad resource saturation."
        )
    elif classification == "RESOURCE_SATURATION":
        recommendations.append(
            "Review mapped-service CPU, heap, GC, executor and thread-pool capacity before release."
        )
    elif classification == "ERROR_DRIVEN_DEGRADATION":
        recommendations.append(
            "Investigate application and dependency errors associated with the latency regression before release."
        )
    elif classification == "THROUGHPUT_PRESSURE":
        recommendations.append(
            "Validate capacity, queueing and backpressure because throughput degradation accompanies the latency regression."
        )
    for regression in list(top_regressions)[:3]:
        action = (
            f"Investigate {_text(regression.get('entity_name'), 'UNKNOWN')} "
            f"{_text(regression.get('metric'), 'UNKNOWN')} variance "
            f"({_number(regression.get('variance_pct')):.2f}%)."
        )
        if action not in recommendations:
            recommendations.append(action)
    for service_name, details in service_health.items():
        if _text(details.get("status")).upper() not in {"", "HEALTHY"}:
            recommendations.append(
                f"Review {service_name} runtime metrics due to {_text(details.get('status'))} service status."
            )
    if anomaly_summary.get("anomaly_count"):
        recommendations.append("Investigate anomalous metrics: " + _text(anomaly_summary.get("message")))
    if classification == "IMPROVEMENT_OBSERVED":
        recommendations.append(
            "Continue controlled-run observation and validate whether the improvement is repeatable."
        )
    elif correlation_summary.get("evidence_gaps"):
        recommendations.append(
            "Collect distributed traces, database timing and downstream dependency latency to increase causal confidence."
        )
    if not recommendations:
        recommendations.append("No major performance concerns were identified from the available evidence.")
    return list(dict.fromkeys(recommendations))


def build_evidence_quality(
    *,
    top_variances: Sequence[Mapping[str, Any]],
    transaction_impacts: Sequence[Mapping[str, Any]],
    service_impacts: Sequence[Mapping[str, Any]],
    anomalies: Sequence[Mapping[str, Any]],
    correlation_summary: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    status = {
        "variance_evidence": "AVAILABLE" if top_variances else "UNAVAILABLE",
        "transaction_evidence": "AVAILABLE" if transaction_impacts else "UNAVAILABLE",
        "service_evidence": "AVAILABLE" if service_impacts else "UNAVAILABLE",
        "anomaly_evidence": "AVAILABLE" if anomalies else "UNAVAILABLE",
        "correlation_evidence": "AVAILABLE" if correlation_summary.get("primary_transaction") else "UNAVAILABLE",
        "similar_executions": "AVAILABLE" if evidence.get("similar_executions") else "UNAVAILABLE",
        "historical_findings": "AVAILABLE" if evidence.get("historical_findings") else "UNAVAILABLE",
        "previous_release_outcomes": "AVAILABLE" if evidence.get("previous_release_outcomes") else "UNAVAILABLE",
    }
    core_keys = (
        "variance_evidence", "transaction_evidence", "service_evidence",
        "anomaly_evidence", "correlation_evidence",
    )
    historical_keys = ("similar_executions", "historical_findings", "previous_release_outcomes")
    core_available = sum(status[key] == "AVAILABLE" for key in core_keys)
    historical_available = sum(status[key] == "AVAILABLE" for key in historical_keys)
    causal_confidence = _number(correlation_summary.get("confidence"))
    completeness = (core_available + historical_available) / len(status)
    confidence_score = round(
        min(1.0, 0.50 * (core_available / len(core_keys)) + 0.30 * causal_confidence + 0.20 * (historical_available / len(historical_keys))),
        3,
    )
    missing = [key for key, value in status.items() if value != "AVAILABLE"]
    data_quality = {
        "status": "COMPLETE" if not missing else "PARTIAL" if core_available >= 3 else "INSUFFICIENT",
        "completeness": round(completeness, 3),
        "available_components": sum(value == "AVAILABLE" for value in status.values()),
        "total_components": len(status),
        "missing_components": missing,
    }
    confidence = {
        "overall": confidence_score,
        "level": "HIGH" if confidence_score >= 0.75 else "MEDIUM" if confidence_score >= 0.45 else "LOW",
        "basis": {
            "current_run_evidence": round(core_available / len(core_keys), 3),
            "causal_confidence": round(causal_confidence, 3),
            "historical_evidence": round(historical_available / len(historical_keys), 3),
        },
        "limitations": list(correlation_summary.get("evidence_gaps") or []) + [f"Missing component: {item}" for item in missing],
    }
    return status, data_quality, confidence


def _safe_bottleneck(run_id: str) -> tuple[dict[str, Any], str | None]:
    try:
        rows = query_bottlenecks(client, run_id)
        result = analyze_bottlenecks(rows, run_id=run_id)
        persist_bottleneck_intelligence(client, result)
        return result, None
    except Exception as exc:
        return {
            "run_id": run_id,
            "primary": None,
            "secondary": [],
            "confidence": 0.0,
        }, f"{type(exc).__name__}: {exc}"


def _safe_correlation(
    run_id: str,
    top_variances: Sequence[Mapping[str, Any]],
    anomaly_summary: Mapping[str, Any],
    bottlenecks: Mapping[str, Any],
    release_impact: Mapping[str, Any],
    service_health: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    try:
        correlation_inputs = query_correlations(client, run_id)
        result = analyze_correlation(
            run_id,
            variance=top_variances,
            anomaly=anomaly_summary,
            bottleneck=bottlenecks,
            release=release_impact,
            service=service_health,
            rows=correlation_inputs,
        )
        persist_correlation_intelligence(client, result)
        return result, None
    except Exception as exc:
        return {
            "schema_version": "aiperf-correlation.v2",
            "run_id": run_id,
            "primary_transaction": None,
            "classification": "INSUFFICIENT_EVIDENCE",
            "correlations": [],
            "statistical_correlations": [],
            "root_cause": "Insufficient correlated signals.",
            "root_cause_hypothesis": {
                "category": "INSUFFICIENT_EVIDENCE",
                "statement": "Correlation intelligence could not complete for this run.",
                "status": "LOW_CONFIDENCE_HYPOTHESIS",
            },
            "causal_confidence": {"score": 0.0, "level": "LOW", "basis": [], "limitations": [str(exc)]},
            "business_impact": "Unable to assess correlated release impact.",
            "observed_facts": [],
            "supporting_signals": [],
            "contradicting_signals": [],
            "evidence_gaps": [f"Correlation engine failure: {type(exc).__name__}: {exc}"],
        }, f"{type(exc).__name__}: {exc}"


def build_findings_package(run_id: str) -> dict[str, Any]:
    selected_run = _safe_run_id(run_id)
    top_variances = get_top_variances(selected_run)
    transaction_impacts = get_transaction_impacts(selected_run)
    service_impacts = get_service_impacts(selected_run)
    anomalies = get_anomalies(selected_run)
    anomaly_summary = build_anomaly_summary(anomalies)
    persisted_health = get_service_health_records(selected_run)
    service_health = build_service_health(service_impacts, persisted_health)
    ai_insights = get_ai_insights(selected_run)
    observed_release_outcome = get_observed_release_outcome(selected_run)
    top_regressions = get_top_regressions(top_variances)
    top_improvements = get_top_improvements(top_variances)
    risk = calculate_risk(top_regressions, anomaly_summary)
    baseline = determine_baseline_status(
        selected_run,
        transaction_impacts,
        service_impacts,
    )
    release_impact = calculate_release_impact(
        risk,
        anomaly_summary,
        service_health,
        baseline,
    )
    bottlenecks, bottleneck_error = _safe_bottleneck(selected_run)
    correlations, correlation_error = _safe_correlation(
        selected_run,
        top_variances,
        anomaly_summary,
        bottlenecks,
        release_impact,
        service_health,
    )
    correlation_summary = summarize_correlations(correlations)
    recommendations = generate_recommendations(
        top_regressions,
        service_health,
        anomaly_summary,
        correlation_summary,
    )
    summary_text = build_summary(top_variances, anomaly_summary)

    package: dict[str, Any] = {
        "schema_version": "aiperf-findings.v2",
        "package_version": FINDINGS_PACKAGE_VERSION,
        "run_id": selected_run,
        "generated_time": datetime.now(UTC).isoformat(),
        "executive_summary": summary_text,
        "risk": risk,
        "release_impact": release_impact,
        "release_decision": release_impact,
        "baseline": baseline,
        "observed_release_outcome": observed_release_outcome,
        "top_regressions": top_regressions,
        "top_improvements": top_improvements,
        "service_health": service_health,
        "anomaly_summary": anomaly_summary,
        "bottleneck_intelligence": bottlenecks,
        "bottleneck_summary": summarize_bottlenecks(bottlenecks),
        "correlation_intelligence": correlations,
        "correlation_summary": correlation_summary,
        "recommended_actions": recommendations,
        "anomalies": anomalies,
        "top_variances": top_variances,
        "transaction_impacts": transaction_impacts,
        "service_impacts": service_impacts,
        "ai_insights": [
            _text(item.get("insight_text"))
            for item in ai_insights if _text(item.get("insight_text"))
        ],
        "similar_executions": [],
        "historical_evidence": [],
        "historical_findings": [],
        "previous_release_outcomes": [],
        "similar_historical_findings": [],
        "historical_outcome_summary": summarize_similar_outcomes([]),
        "evidence_provenance": {
            "current_findings": MEASUREMENT_NAME,
            "similar_executions": "aiperf_similar_execution",
            "historical_findings": "historical_findings_search",
            "previous_release_outcomes": "aiperf_release_outcome",
            "correlation_intelligence": "aiperf_correlation_intelligence",
        },
        "component_status": {},
        "data_quality": {},
        "confidence": {},
        "processing_warnings": [
            warning for warning in (
                f"Bottleneck intelligence: {bottleneck_error}" if bottleneck_error else None,
                f"Correlation intelligence: {correlation_error}" if correlation_error else None,
            ) if warning
        ],
    }

    try:
        evidence = prepare_evidence(client, selected_run, package)
    except Exception as exc:
        evidence = {
            "historical_findings": [],
            "similar_executions": [],
            "previous_release_outcomes": [],
            "evidence_provenance": package["evidence_provenance"],
        }
        package["processing_warnings"].append(
            f"Evidence orchestration: {type(exc).__name__}: {exc}"
        )

    package["historical_findings"] = _unique_by_run(
        evidence.get("historical_findings", []), selected_run
    )
    package["historical_evidence"] = package["historical_findings"]
    package["similar_executions"] = _unique_by_run(
        evidence.get("similar_executions", []), selected_run
    )
    package["previous_release_outcomes"] = _unique_by_run(
        evidence.get("previous_release_outcomes", []), selected_run
    )
    package["evidence_provenance"] = evidence.get(
        "evidence_provenance", package["evidence_provenance"]
    )
    status, quality, confidence = build_evidence_quality(
        top_variances=top_variances,
        transaction_impacts=transaction_impacts,
        service_impacts=service_impacts,
        anomalies=anomalies,
        correlation_summary=correlation_summary,
        evidence=package,
    )
    package["component_status"] = status
    package["data_quality"] = quality
    package["confidence"] = confidence
    return package


def persist_findings_package(package: Mapping[str, Any]) -> None:
    run_id = _safe_run_id(package.get("run_id"))
    findings_json = json.dumps(
        package,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    validated_package = json.loads(findings_json)
    if not isinstance(validated_package, dict):
        raise TypeError("Serialized findings package must have a JSON object root")
    if validated_package.get("run_id") != run_id:
        raise ValueError("Serialized findings package RUN_ID validation failed")

    point = {
        "measurement": MEASUREMENT_NAME,
        "tags": {
            "run_id": run_id,
            "risk_level": _text((package.get("risk") or {}).get("level"), "UNKNOWN"),
            "release_decision": _text((package.get("release_impact") or {}).get("decision"), "UNKNOWN"),
            "data_quality": _text((package.get("data_quality") or {}).get("status"), "UNKNOWN"),
        },
        "fields": {
            "schema_version": _text(package.get("schema_version"), "aiperf-findings.v2"),
            "package_version": _text(package.get("package_version"), FINDINGS_PACKAGE_VERSION),
            "summary": _text(package.get("executive_summary")),
            "confidence_score": _number((package.get("confidence") or {}).get("overall")),
            "historical_observed_matches": int(_number(
                (package.get("historical_outcome_summary") or {}).get("observed_matches")
            )),
            "historical_success_rate": _number(
                (package.get("historical_outcome_summary") or {}).get("success_rate")
            ),
            "findings_json": findings_json,
        },
    }
    written = client.write_points([point])
    if written is False:
        raise RuntimeError("InfluxDB rejected the findings package")


def enrich_knowledge_layer(package: dict[str, Any]) -> list[str]:
    """Persist the embedding and attach outcome-aware historical matches."""
    warnings: list[str] = []
    try:
        text = package_to_text(package)
        vector = create_embedding(text)
        if not vector:
            warnings.append(
                "Embedding generation was skipped because no vector was returned."
            )
            return warnings

        run_id = _safe_run_id(package.get("run_id"))
        observed = package.get("observed_release_outcome") or {}
        observed_outcome = _text(observed.get("outcome"), "NOT_OBSERVED")

        store_finding(
            client,
            run_id,
            text,
            vector,
            risk_level=_text((package.get("risk") or {}).get("level"), "UNKNOWN"),
            recommendations=package.get("recommended_actions", []),
            outcome=observed_outcome,
        )

        similar = find_similar_findings(
            client,
            run_id,
            query_vector=vector,
        )
        package["similar_historical_findings"] = _unique_by_run(similar, run_id)
        package["historical_outcome_summary"] = summarize_similar_outcomes(
            package["similar_historical_findings"]
        )
        package["evidence_provenance"]["historical_outcomes"] = (
            "aiperf_release_outcome"
        )
        _apply_outcome_confidence(package)
    except Exception as exc:
        warnings.append(f"Knowledge-layer enrichment: {type(exc).__name__}: {exc}")
    return warnings


def _apply_outcome_confidence(package: dict[str, Any]) -> None:
    """Calibrate package confidence using observed similar-run outcomes.

    Outcome evidence can strengthen confidence only when at least three similar
    runs have observed outcomes. It can never replace current-run evidence.
    """
    confidence = dict(package.get("confidence") or {})
    summary = dict(package.get("historical_outcome_summary") or {})
    observed = int(_number(summary.get("observed_matches")))
    base = _number(confidence.get("overall"))

    if observed <= 0:
        confidence.setdefault("limitations", []).append(
            "No observed outcomes were available among similar historical runs."
        )
        confidence["outcome_evidence"] = {
            "observed_matches": 0,
            "coverage": 0.0,
            "contribution": 0.0,
        }
        package["confidence"] = confidence
        return

    total = max(1, int(_number(summary.get("total_similar_matches"))))
    coverage = min(1.0, observed / total)
    sample_strength = min(1.0, observed / 5.0)
    weighted_success = _number(summary.get("weighted_success_rate")) / 100.0
    outcome_signal = min(1.0, coverage * sample_strength)
    contribution = 0.15 * outcome_signal
    calibrated = min(1.0, base * (1.0 - contribution) + weighted_success * contribution)

    confidence["overall"] = round(calibrated, 3)
    confidence["level"] = (
        "HIGH" if calibrated >= 0.75 else "MEDIUM" if calibrated >= 0.45 else "LOW"
    )
    confidence.setdefault("basis", {})["historical_outcomes"] = round(
        outcome_signal, 3
    )
    confidence["outcome_evidence"] = {
        "observed_matches": observed,
        "total_matches": total,
        "coverage": round(coverage, 3),
        "sample_strength": round(sample_strength, 3),
        "weighted_success_rate": round(weighted_success, 3),
        "contribution": round(contribution, 3),
    }
    if observed < 3:
        confidence.setdefault("limitations", []).append(
            "Historical outcome confidence is LOW because fewer than three "
            "similar runs have observed outcomes."
        )
    package["confidence"] = confidence


def _print_summary(package: Mapping[str, Any]) -> None:
    risk = package.get("risk") or {}
    release = package.get("release_impact") or {}
    anomaly = package.get("anomaly_summary") or {}
    correlation = package.get("correlation_summary") or {}
    print("\n===================================")
    print("AiPERF FINDINGS PACKAGE")
    print("===================================")
    print(f"RUN_ID              : {package.get('run_id')}")
    print(f"Risk                : {risk.get('level')} ({risk.get('score')})")
    print(f"Release Decision    : {release.get('decision')}")
    print(f"Baseline Status     : {release.get('baseline_status')}")
    print(f"Decision Type       : {release.get('decision_type')}")
    print(f"Anomaly Status      : {anomaly.get('status')}")
    print(f"Correlation Class   : {correlation.get('classification')}")
    print(f"Causal Confidence   : {correlation.get('confidence_level')} ({correlation.get('confidence')})")
    print(f"Package Confidence  : {(package.get('confidence') or {}).get('level')} ({(package.get('confidence') or {}).get('overall')})")
    outcome_summary = package.get("historical_outcome_summary") or {}
    print(
        "Historical Outcomes : "
        f"{outcome_summary.get('observed_matches', 0)} observed; "
        f"success rate {outcome_summary.get('success_rate', 0.0)}%"
    )
    print("\nEXECUTIVE SUMMARY")
    print("-----------------------------")
    print(package.get("executive_summary", ""))
    print("\nCORRELATION SUMMARY")
    print("-----------------------------")
    print(correlation.get("summary", "Unavailable"))
    print("\nRECOMMENDED ACTIONS")
    print("-----------------------------")
    for action in package.get("recommended_actions", []):
        print(f"- {action}")
    for warning in package.get("processing_warnings", []):
        print(f"WARNING: {warning}")


def main(influx_client: Any = None, requested_run_id: str | None = None) -> dict[str, Any]:
    global client
    if influx_client is None and InfluxDBClient is None:
        raise RuntimeError("Install the 'influxdb' package to run the findings package")
    owns_client = influx_client is None
    client = influx_client or InfluxDBClient(host=DB_HOST, port=DB_PORT, database=DB_NAME)
    try:
        run_id = get_latest_run_id(requested_run_id or os.getenv("RUN_ID"))
        package = build_findings_package(run_id)
        package["processing_warnings"].extend(enrich_knowledge_layer(package))
        persist_findings_package(package)
        _print_summary(package)
        print("\nWritten To: aiperf_findings_package")
        return package
    except Exception as exc:
        print("\n===================================")
        print("FINDINGS PACKAGE FAILED")
        print("===================================")
        print(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if owns_client and client is not None:
            client.close()


def cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the AiPERF findings package")
    parser.add_argument("--run-id", default=os.getenv("RUN_ID"))
    args = parser.parse_args(argv)
    main(requested_run_id=args.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())

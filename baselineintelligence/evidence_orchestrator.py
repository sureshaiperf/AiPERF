"""Prepare bounded, provenance-aware evidence for AI consumers.

The orchestrator is the only boundary between persisted performance data and
GPT.  It deliberately emits summaries and evidence references, never raw
metric rows.
"""
from __future__ import annotations

import json
from typing import Any

try:
    from .historical_findings_search import search_findings
    from .similar_execution_contract import canonical_similar_execution
except ImportError:
    from historical_findings_search import search_findings
    from similar_execution_contract import canonical_similar_execution


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _similar_executions(client: Any, run_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return the canonical, metric-free similar execution contract."""
    try:
        rows = client.query(
            'SELECT * FROM "aiperf_similar_execution" '
            f"WHERE current_run_id='{run_id}' ORDER BY time DESC LIMIT {int(limit)}"
        ).get_points()
    except Exception:
        rows = []
    result = []
    for row in rows:
        result.append(canonical_similar_execution(row, run_id))
    return result


def _previous_outcomes(client: Any, run_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Load historical release outcomes separately from the predicted decision."""
    rows = []
    for measurement in ("aiperf_release_outcome",):
        try:
            query = f'SELECT * FROM "{measurement}" ORDER BY time DESC LIMIT {int(limit) + 1}'
            rows = list(client.query(query).get_points())
        except Exception:
            rows = []
        if rows:
            break
    outcomes = []
    for row in rows:
        row_run = str(row.get("run_id", ""))
        if row_run == str(run_id):
            continue
        outcomes.append({
            "run_id": row_run or "UNKNOWN",
            "outcome": row.get("outcome", "UNKNOWN"),
            "source": measurement,
            "evidence_source": measurement,
        })
    return outcomes[:limit]


def _metric_free_findings(package: dict[str, Any]) -> dict[str, Any]:
    """Keep findings useful to GPT while excluding raw metric observations."""
    allowed = (
        "run_id", "package_version", "generated_time", "executive_summary",
        "risk", "release_impact", "anomaly_summary", "bottleneck_summary",
        "correlation_summary", "transaction_service_mapping",
        "recommended_actions",
    )
    return {key: package.get(key) for key in allowed if key in package}


def _metric_free_current_findings(package: Any) -> Any:
    if isinstance(package, list):
        return [
            _metric_free_findings(item)
            for item in package
            if isinstance(item, dict)
        ]
    return _metric_free_findings(package)


def prepare_evidence(client: Any, run_id: str, current_findings: dict[str, Any] | str,
                     *, limit: int = 5) -> dict[str, Any]:
    """Build the central evidence contract consumed by release-advisor prompts."""
    package = _json(current_findings)
    if not isinstance(package, (dict, list)):
        package = {"executive_summary": str(current_findings)}
    if isinstance(package, list):
        query = " ".join(
            str(item.get("executive_summary", ""))
            for item in package
            if isinstance(item, dict)
        )
    else:
        query = str(package.get("executive_summary", "performance release evidence"))
    try:
        historical = search_findings(client, query, limit=limit)
    except Exception:
        historical = []
    historical = [{
        "run_id": item.get("run_id", "UNKNOWN"),
        "relevance": item.get("relevance", 0),
        "risk": item.get("risk_level", item.get("risk", "UNKNOWN")),
        "outcome": item.get("outcomes", item.get("outcome", "UNKNOWN")),
        "evidence_source": "historical_findings_search",
    } for item in historical if str(item.get("run_id")) != str(run_id)]
    return {
        "contract_version": "aiperf-evidence.v1",
        "current_findings": _metric_free_current_findings(package),
        "similar_executions": _similar_executions(client, run_id, limit),
        "historical_findings": historical,
        "previous_release_outcomes": _previous_outcomes(client, run_id),
        "evidence_provenance": {
            "current_findings": "aiperf_findings_package",
            "similar_executions": "aiperf_similar_execution",
            "historical_findings": "historical_findings_search",
            "transaction_service_mapping": (
                package.get("transaction_service_mapping", {}).get("source")
                if isinstance(package, dict)
                else None
            ),
        },
    }

"""AiPERF deterministic correlation and causal-intelligence engine.

This module correlates transaction regressions with relevant microservice
signals for one RUN_ID. It deliberately separates observed facts,
correlated/contradicting evidence, and root-cause hypotheses.

Public functions are backward compatible with the existing findings package:
    calculate_correlations
    query_correlations
    analyze_correlation
    persist_correlation_intelligence
    summarize_correlations

The module contains no GPT dependency. All conclusions are deterministic and
bounded by the evidence supplied or retrieved from InfluxDB.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ENGINE_VERSION = "2.0.0"
DEFAULT_MEASUREMENT = "aiperf_correlation_intelligence"
DEFAULT_MINIMUM_SAMPLES = 3
DEFAULT_REGRESSION_THRESHOLD_PCT = 5.0
DEFAULT_STRONG_REGRESSION_PCT = 20.0
DEFAULT_RESOURCE_PRESSURE_PCT = 80.0
DEFAULT_STRONG_CORRELATION = 0.70

DEFAULT_TRANSACTION_SERVICE_MAP: dict[str, str] = {
    "user": "user-service",
    "users": "user-service",
    "product": "product-service",
    "products": "product-service",
    "order": "order-service",
    "orders": "order-service",
    "gateway": "gateway",
}

LATENCY_METRICS = {
    "avg", "avg_rt", "average", "average_response_time", "response_time",
    "latency", "mean", "p90", "p95", "p99", "max", "max_rt",
    "avg_response_time_ms",
}
TAIL_METRICS = {"p95", "p99", "max", "max_rt"}
ERROR_METRICS = {"error", "errors", "error_pct", "error_rate", "failure_rate"}
THROUGHPUT_METRICS = {"throughput", "tps", "requests_per_second", "request_rate"}
CPU_METRICS = {"cpu", "cpu_pct", "system_cpu", "process_cpu", "system_cpu_pct", "process_cpu_pct"}
HEAP_METRICS = {"heap", "heap_pct", "jvm_memory", "jvm_memory_pct", "memory_pct"}
GC_METRICS = {"gc", "gc_overhead", "gc_pct", "jvm_gc_overhead"}
THREAD_METRICS = {"threads", "jvm_threads", "live_threads", "executor_active", "active_threads"}
REQUEST_METRICS = {"request_count", "http_requests", "active_requests", "executor_active"}


def _number(value: Any, default: float | None = None) -> float | None:
    """Return a finite float or the supplied default."""
    if value is None or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _normalise(value: Any) -> str:
    text = _text(value).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _unique_strings(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _safe_run_id(run_id: Any) -> str:
    value = _text(run_id)
    if not value:
        raise ValueError("run_id is required")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ValueError("run_id contains unsupported characters")
    return value


def _mapping_file_path() -> Path:
    configured = os.getenv("AIPERF_TRANSACTION_SERVICE_MAPPING_FILE", "").strip()
    return Path(configured).expanduser() if configured else Path(__file__).resolve().with_name("transaction_service_mapping.json")


def load_mapping_contract() -> dict[str, Any]:
    """Load and validate the authoritative transaction-service mapping file."""
    path = _mapping_file_path()
    if not path.is_file():
        raise FileNotFoundError(f"Transaction-service mapping file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid transaction-service mapping JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Mapping contract root must be an object")
    transactions = payload.get("transactions")
    if not isinstance(transactions, dict) or not transactions:
        raise ValueError("Mapping contract transactions must be a non-empty object")
    default_entry = _text(payload.get("default_entry_service"), "gateway")
    normalized = {}
    for name, raw in transactions.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"Mapping entry must be an object: {name}")
        target = _text(raw.get("target_service"))
        if not target:
            raise ValueError(f"target_service is required: {name}")
        entry = _text(raw.get("entry_service"), default_entry)
        path_items = raw.get("service_path") or [entry, target]
        if not isinstance(path_items, list) or not all(_text(item) for item in path_items):
            raise ValueError(f"service_path must be a non-empty string list: {name}")
        normalized[_normalise(name)] = {
            "transaction": _text(name),
            "method": _text(raw.get("method")),
            "path": _text(raw.get("path")),
            "entry_service": entry,
            "target_service": target,
            "service_path": [_text(item) for item in path_items],
            "business_criticality": _text(raw.get("business_criticality"), "UNSPECIFIED").upper(),
        }
    return {
        "status": "AVAILABLE",
        "source": str(path),
        "schema_version": _text(payload.get("schema_version"), "1.0"),
        "default_entry_service": default_entry,
        "transactions": normalized,
    }


def mapping_status(transaction_names: Iterable[str] = ()) -> dict[str, Any]:
    names = list(
    dict.fromkeys(
        _text(name)
        for name in transaction_names
        if _text(name)
    )
)
    try:
        contract = load_mapping_contract()
        mapped = [name for name in names if _normalise(name) in contract["transactions"]]
        unmapped = [name for name in names if _normalise(name) not in contract["transactions"]]
        return {
            "status": "AVAILABLE",
            "source": contract["source"],
            "schema_version": contract["schema_version"],
            "total_contract_mappings": len(contract["transactions"]),
            "evaluated_transaction_count": len(names),
            "mapped_transaction_count": len(mapped),
            "unmapped_transaction_count": len(unmapped),
            "mapped_transactions": mapped,
            "unmapped_transactions": unmapped,
            "default_entry_service": contract["default_entry_service"],
        }
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "source": str(_mapping_file_path()),
            "schema_version": None,
            "total_contract_mappings": 0,
            "evaluated_transaction_count": len(names),
            "mapped_transaction_count": 0,
            "unmapped_transaction_count": len(names),
            "mapped_transactions": [],
            "unmapped_transactions": names,
            "default_entry_service": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }


def _read_mapping() -> dict[str, str]:
    """Return exact contract mappings plus optional legacy overrides."""
    mapping = dict(DEFAULT_TRANSACTION_SERVICE_MAP)
    try:
        contract = load_mapping_contract()
        for key, details in contract["transactions"].items():
            mapping[key] = details["target_service"]
    except (FileNotFoundError, ValueError):
        pass
    raw = os.getenv("AIPERF_TRANSACTION_SERVICE_MAP_JSON", "").strip()
    if raw:
        try:
            configured = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("AIPERF_TRANSACTION_SERVICE_MAP_JSON is invalid JSON") from exc
        if not isinstance(configured, dict):
            raise ValueError("AIPERF_TRANSACTION_SERVICE_MAP_JSON must be a JSON object")
        for key, value in configured.items():
            if _text(key) and _text(value):
                mapping[_normalise(key)] = _text(value)
    return mapping


def mapping_details(transaction: str) -> dict[str, Any] | None:
    try:
        return dict(load_mapping_contract()["transactions"].get(_normalise(transaction)) or {}) or None
    except (FileNotFoundError, ValueError):
        return None

def map_transaction_to_service(
    transaction: str,
    mapping: Mapping[str, str] | None = None,
) -> tuple[str | None, str]:
    """Resolve a transaction to a service using exact then token matching."""
    configured = dict(mapping or _read_mapping())
    normalised_transaction = _normalise(transaction)
    if normalised_transaction in configured:
        return configured[normalised_transaction], "EXACT_MAPPING"

    tokens = set(normalised_transaction.split("_"))
    candidates: list[tuple[int, int, str]] = []
    for key, service_name in configured.items():
        normalised_key = _normalise(key)
        key_tokens = set(normalised_key.split("_"))
        if normalised_key and normalised_key in normalised_transaction:
            candidates.append((2, len(normalised_key), service_name))
        elif key_tokens and key_tokens.intersection(tokens):
            candidates.append((1, len(normalised_key), service_name))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][2], "TOKEN_MAPPING"
    return None, "UNMAPPED"


def calculate_correlations(
    rows: Iterable[Mapping[str, Any]],
    metrics: Sequence[str] | None = None,
    *,
    minimum_samples: int = DEFAULT_MINIMUM_SAMPLES,
) -> list[dict[str, Any]]:
    """Calculate Pearson correlations for aligned numeric observations.

    This remains available for genuine time-series inputs. A correlation is
    emitted only when at least ``minimum_samples`` aligned finite values exist
    and both series have non-zero variance.
    """
    observations = [dict(row) for row in rows]
    if minimum_samples < 3:
        minimum_samples = 3
    if not observations:
        return []

    names = list(metrics or observations[0].keys())
    output: list[dict[str, Any]] = []
    for index, metric_a in enumerate(names):
        for metric_b in names[index + 1 :]:
            pairs: list[tuple[float, float]] = []
            for row in observations:
                left = _number(row.get(metric_a))
                right = _number(row.get(metric_b))
                if left is not None and right is not None:
                    pairs.append((left, right))
            if len(pairs) < minimum_samples:
                continue

            left_mean = sum(left for left, _ in pairs) / len(pairs)
            right_mean = sum(right for _, right in pairs) / len(pairs)
            numerator = sum(
                (left - left_mean) * (right - right_mean)
                for left, right in pairs
            )
            denominator = math.sqrt(
                sum((left - left_mean) ** 2 for left, _ in pairs)
                * sum((right - right_mean) ** 2 for _, right in pairs)
            )
            if denominator <= 0:
                continue
            coefficient = max(-1.0, min(1.0, numerator / denominator))
            output.append(
                {
                    "metric_a": str(metric_a),
                    "metric_b": str(metric_b),
                    "correlation": round(coefficient, 6),
                    "sample_count": len(pairs),
                    "strength": (
                        "STRONG" if abs(coefficient) >= 0.70
                        else "MODERATE" if abs(coefficient) >= 0.40
                        else "WEAK"
                    ),
                    "direction": "POSITIVE" if coefficient >= 0 else "NEGATIVE",
                }
            )
    return sorted(output, key=lambda item: abs(item["correlation"]), reverse=True)


def _query_points(client: Any, query: str) -> list[dict[str, Any]]:
    result = client.query(query)
    return [dict(row) for row in result.get_points()]


def query_correlations(
    client: Any,
    run_id: str,
    measurement: str = "aiperf_execution_history",
) -> dict[str, Any]:
    """Load bounded, run-scoped inputs required by causal analysis.

    ``measurement`` is retained for backward compatibility and is used for the
    optional time-series/execution observations.
    """
    safe_run_id = _safe_run_id(run_id)
    sources: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []

    queries = {
        "transaction_rows": (
            'SELECT * FROM "aiperf_transaction_comparison" '
            f"WHERE current_run_id='{safe_run_id}'"
        ),
        "service_rows": (
            'SELECT * FROM "aiperf_service_comparison" '
            f"WHERE current_run_id='{safe_run_id}'"
        ),
        "service_metric_rows": (
            'SELECT * FROM "aiperf_service_metrics" '
            f"WHERE run_id='{safe_run_id}'"
        ),
        "execution_rows": (
            f'SELECT * FROM "{measurement}" '
            f"WHERE run_id='{safe_run_id}' ORDER BY time ASC"
        ),
    }
    for source_name, query in queries.items():
        try:
            sources[source_name] = _query_points(client, query)
        except Exception as exc:  # one unavailable source must not hide others
            sources[source_name] = []
            errors.append(f"{source_name}: {type(exc).__name__}: {exc}")

    sources["run_id"] = safe_run_id
    sources["query_errors"] = errors
    sources["statistical_correlations"] = calculate_correlations(
        sources["execution_rows"]
    )
    return sources


def _entity_type(row: Mapping[str, Any]) -> str:
    explicit = _normalise(row.get("entity_type"))
    if explicit:
        return explicit
    if row.get("transaction") is not None:
        return "transaction"
    if row.get("service_name") is not None:
        return "service"
    return "unknown"


def _entity_name(row: Mapping[str, Any]) -> str:
    return _text(
        row.get("entity_name")
        or row.get("transaction")
        or row.get("service_name")
        or row.get("service")
        or "UNKNOWN"
    )


def _metric_name(row: Mapping[str, Any]) -> str:
    return _normalise(row.get("metric") or row.get("metric_name") or "unknown")


def _variance(row: Mapping[str, Any]) -> float:
    return float(_number(row.get("variance_pct"), 0.0) or 0.0)


def _normalise_transaction_rows(
    variance: Iterable[Mapping[str, Any]],
    transaction_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in list(variance or []) + list(transaction_rows or []):
        row = dict(raw)
        if _entity_type(row) != "transaction":
            continue
        output.append(
            {
                "entity": _entity_name(row),
                "metric": _metric_name(row),
                "variance_pct": _variance(row),
                "current_value": _number(row.get("current_value")),
                "reference_value": _number(
                    row.get("previous_value", row.get("baseline_value"))
                ),
                "source": row.get("source", "aiperf_transaction_comparison"),
            }
        )
    # Prefer richer duplicate rows containing current/reference values.
    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for row in output:
        key = (row["entity"], row["metric"])
        existing = deduplicated.get(key)
        if existing is None or (
            existing.get("current_value") is None
            and row.get("current_value") is not None
        ):
            deduplicated[key] = row
    return list(deduplicated.values())


def _normalise_service_rows(
    variance: Iterable[Mapping[str, Any]],
    service_rows: Iterable[Mapping[str, Any]],
    service_health: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    # Service comparison evidence is authoritative. Do not import service
    # variance-ranking rows because they may be independently calculated and
    # conflict with the run-scoped aiperf_service_comparison measurement.

    comparison_pairs = (
        ("avg_rt", "avg_rt_baseline", "avg_rt_current", "avg_rt_variance_pct", True),
        ("request_count", "request_count_baseline", "request_count_current", "request_count_variance_pct", None),
        ("heap_pct", "heap_pct_baseline", "heap_pct_current", "heap_pct_variance_pct", True),
        ("gc_overhead", "gc_baseline", "gc_current", "gc_variance_pct", True),
        ("active_requests", "active_requests_baseline", "active_requests_current", None, True),
        ("executor_active", "executor_active_baseline", "executor_active_current", None, True),
    )
    for raw in service_rows or []:
        row = dict(raw)
        service_name = _text(row.get("service_name") or row.get("entity_name") or "UNKNOWN")
        for metric, reference_key, current_key, variance_key, default_available in comparison_pairs:
            reference = _number(row.get(reference_key))
            current = _number(row.get(current_key))
            if reference is None and current is None:
                continue
            available = bool(default_available)
            if metric == "request_count":
                available = bool(row.get("request_count_comparable", False))
            variance_value = _number(row.get(variance_key)) if variance_key else None
            if variance_value is None and reference not in (None, 0) and current is not None:
                variance_value = ((current - reference) / abs(reference)) * 100.0
            output.append(
                {
                    "service_name": service_name,
                    "metric": metric,
                    "variance_pct": float(variance_value or 0.0),
                    "current_value": current,
                    "reference_value": reference,
                    "available": available,
                    "source": "aiperf_service_comparison",
                }
            )

    if isinstance(service_health, Mapping):
        health_items = service_health.items()
    else:
        health_items = (
            (_text(item.get("service_name") or item.get("entity_name") or "UNKNOWN"), item)
            for item in (service_health or [])
            if isinstance(item, Mapping)
        )
    health_metric_keys = (
        "cpu_pct", "system_cpu", "process_cpu", "heap_pct", "gc_overhead",
        "active_requests", "executor_active", "jvm_threads", "request_count",
    )
    for service_name, raw_health in health_items:
        if not isinstance(raw_health, Mapping):
            continue
        for key in health_metric_keys:
            current = _number(raw_health.get(key))
            if current is None:
                continue
            output.append(
                {
                    "service_name": _text(service_name, "UNKNOWN"),
                    "metric": _normalise(key),
                    "variance_pct": float(
                        _number(raw_health.get(f"{key}_variance_pct"), 0.0) or 0.0
                    ),
                    "current_value": current,
                    "reference_value": _number(raw_health.get(f"{key}_baseline")),
                    "available": True,
                    "status": _text(raw_health.get("status"), "UNKNOWN"),
                    "source": "aiperf_service_health",
                }
            )
    return output


def _metric_group(metric: str) -> str:
    name = _normalise(metric)
    if name in ERROR_METRICS or "error" in name or "fail" in name:
        return "ERROR"
    if name in THROUGHPUT_METRICS or "throughput" in name or name == "tps":
        return "THROUGHPUT"
    if name in CPU_METRICS or "cpu" in name:
        return "CPU"
    if name in HEAP_METRICS or "heap" in name or "memory" in name:
        return "HEAP"
    if name in GC_METRICS or name.startswith("gc_") or "gc_overhead" in name:
        return "GC"
    if name in THREAD_METRICS or "thread" in name or "executor" in name:
        return "THREAD"
    if name in REQUEST_METRICS or "request_count" in name or "active_request" in name:
        return "REQUEST"
    if name in LATENCY_METRICS or name.startswith("p9") or "latency" in name or "response_time" in name or name.endswith("_rt"):
        return "LATENCY"
    return "OTHER"


def _find_metric(
    rows: Iterable[Mapping[str, Any]],
    metric_names: set[str],
) -> dict[str, Any] | None:
    normalised_names = {_normalise(name) for name in metric_names}
    candidates = [
        dict(row) for row in rows
        if _normalise(row.get("metric")) in normalised_names
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: abs(_variance(row)), reverse=True)[0]


def _format_fact(row: Mapping[str, Any], *, service: bool = False) -> str:
    entity = _text(row.get("service_name") if service else row.get("entity"), "UNKNOWN")
    metric = _text(row.get("metric"), "metric")
    variance = float(_number(row.get("variance_pct"), 0.0) or 0.0)
    direction = "increased" if variance > 0 else "improved" if variance < 0 else "did not change"
    current = _number(row.get("current_value"))
    reference = _number(row.get("reference_value"))
    values = ""
    if current is not None and reference is not None:
        values = f" ({reference:.2f} to {current:.2f})"
    return f"{entity} {metric} {direction} by {abs(variance):.2f}%{values}."


def _anomaly_present(anomaly: Mapping[str, Any] | None) -> bool:
    if not anomaly:
        return False
    count = _number(anomaly.get("count", anomaly.get("anomaly_count")), 0.0) or 0.0
    status = _normalise(anomaly.get("status"))
    return count > 0 or status in {"anomalous", "warning", "critical", "anomaly"}


def _classification(
    primary: Mapping[str, Any] | None,
    transaction_rows: list[dict[str, Any]],
    mapped_service_rows: list[dict[str, Any]],
    anomaly: Mapping[str, Any] | None,
) -> tuple[str, str, str]:
    if not primary:
        return (
            "INSUFFICIENT_EVIDENCE",
            "INSUFFICIENT_EVIDENCE",
            "No transaction regression was available for causal assessment.",
        )

    transaction_name = primary["entity"]
    same_transaction = [row for row in transaction_rows if row["entity"] == transaction_name]
    primary_metric = _normalise(primary["metric"])
    primary_variance = _variance(primary)
    errors = [row for row in same_transaction if _metric_group(row["metric"]) == "ERROR"]
    throughput = [row for row in same_transaction if _metric_group(row["metric"]) == "THROUGHPUT"]
    p95 = _find_metric(same_transaction, {"p95"})
    average = _find_metric(same_transaction, {"avg", "avg_rt", "average", "response_time"})

    resources = [row for row in mapped_service_rows if _metric_group(row["metric"]) in {"CPU", "HEAP", "GC", "THREAD"}]
    resource_pressure = any(
        (_number(row.get("current_value"), 0.0) or 0.0) >= DEFAULT_RESOURCE_PRESSURE_PCT
        or _variance(row) >= DEFAULT_STRONG_REGRESSION_PCT
        for row in resources
        if row.get("available", True)
    )
    error_regression = any(_variance(row) >= DEFAULT_REGRESSION_THRESHOLD_PCT for row in errors)
    throughput_drop = any(_variance(row) <= -DEFAULT_REGRESSION_THRESHOLD_PCT for row in throughput)
    tail_only = (
        primary_metric in TAIL_METRICS
        and primary_variance >= DEFAULT_STRONG_REGRESSION_PCT
        and (p95 is None or _variance(p95) < DEFAULT_REGRESSION_THRESHOLD_PCT or primary_metric == "p95")
        and (average is None or _variance(average) < DEFAULT_STRONG_REGRESSION_PCT)
    )

    if error_regression:
        return (
            "ERROR_DRIVEN_DEGRADATION",
            "APPLICATION_OR_DEPENDENCY_FAILURE",
            "Latency degradation is accompanied by an error-rate regression.",
        )
    if resource_pressure:
        return (
            "RESOURCE_SATURATION",
            "SERVICE_RESOURCE_PRESSURE",
            "The mapped service shows resource pressure consistent with the transaction regression.",
        )
    if throughput_drop:
        return (
            "THROUGHPUT_PRESSURE",
            "CAPACITY_OR_BACKPRESSURE",
            "The transaction regression is accompanied by reduced throughput.",
        )
    if tail_only:
        return (
            "ISOLATED_TAIL_LATENCY",
            "INTERMITTENT_REQUEST_PATH_DELAY",
            "The evidence indicates long-tail request delay rather than system-wide resource saturation.",
        )
    if mapped_service_rows:
        service_latency = [row for row in mapped_service_rows if _metric_group(row["metric"]) == "LATENCY"]
        if any(_variance(row) >= DEFAULT_REGRESSION_THRESHOLD_PCT for row in service_latency):
            return (
                "TRANSACTION_SERVICE_LATENCY",
                "APPLICATION_OR_DOWNSTREAM_LATENCY",
                "Transaction and mapped-service latency moved in the same degrading direction.",
            )
    if _anomaly_present(anomaly):
        return (
            "ANOMALOUS_PERFORMANCE_DEGRADATION",
            "INTERMITTENT_OR_NOVEL_BEHAVIOUR",
            "The regression is supported by anomaly evidence but lacks a correlated service signal.",
        )
    return (
        "UNEXPLAINED_TRANSACTION_REGRESSION",
        "APPLICATION_OR_DOWNSTREAM_LATENCY",
        "A transaction regression is established, but the available runtime evidence does not identify a specific cause.",
    )


def _confidence(
    *,
    primary: Mapping[str, Any] | None,
    mapped_service: str | None,
    mapping_method: str,
    mapped_service_rows: list[dict[str, Any]],
    supporting_count: int,
    contradicting_count: int,
    anomaly_agreement: bool,
    statistical_correlations: list[dict[str, Any]],
) -> dict[str, Any]:
    score = 0.0
    basis: list[str] = []
    limitations: list[str] = []

    if primary:
        score += 0.25
        basis.append("A run-scoped transaction regression is available.")
    else:
        limitations.append("No run-scoped transaction regression is available.")

    if mapped_service:
        mapping_score = 0.15 if mapping_method == "EXACT_MAPPING" else 0.10
        score += mapping_score
        basis.append(f"Transaction-to-service mapping is available using {mapping_method.lower()}.")
    else:
        limitations.append("The affected transaction could not be mapped to a service.")

    available_groups = {
        _metric_group(row["metric"])
        for row in mapped_service_rows
        if row.get("available", True)
    }
    if "LATENCY" in available_groups:
        score += 0.12
        basis.append("Mapped-service latency evidence is available.")
    else:
        limitations.append("Mapped-service latency evidence is unavailable.")
    runtime_groups = available_groups.intersection({"CPU", "HEAP", "GC", "THREAD", "REQUEST"})
    if runtime_groups:
        score += min(0.18, 0.04 * len(runtime_groups))
        basis.append("Runtime evidence is available for " + ", ".join(sorted(runtime_groups)) + ".")
    else:
        limitations.append("No mapped-service runtime resource evidence is available.")

    if supporting_count:
        score += min(0.15, 0.05 * supporting_count)
        basis.append(f"{supporting_count} supporting signal(s) were identified.")
    if anomaly_agreement:
        score += 0.08
        basis.append("Anomaly evidence agrees with the degradation.")
    if statistical_correlations:
        strongest = abs(float(statistical_correlations[0]["correlation"]))
        samples = int(statistical_correlations[0].get("sample_count", 0))
        statistical_weight = min(0.12, strongest * 0.08 + min(samples, 20) / 500)
        score += statistical_weight
        basis.append("Aligned time-series correlation evidence is available.")
    else:
        limitations.append("No sufficient aligned time-series correlation was available.")

    # Contradicting evidence reduces causal certainty, but does not erase the
    # confidence that the transaction regression itself was observed.
    score -= min(0.15, 0.03 * contradicting_count)
    score = round(max(0.0, min(1.0, score)), 3)
    level = "HIGH" if score >= 0.75 else "MEDIUM" if score >= 0.45 else "LOW"
    return {
        "score": score,
        "level": level,
        "basis": basis,
        "limitations": limitations,
    }


def analyze_correlation(
    run_id: str,
    *,
    variance: Iterable[Mapping[str, Any]] | None = None,
    anomaly: Mapping[str, Any] | None = None,
    bottleneck: Mapping[str, Any] | None = None,
    release: Mapping[str, Any] | None = None,
    service: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    rows: Any = (),
    transaction_service_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build explainable transaction-to-service causal intelligence."""
    safe_run_id = _safe_run_id(run_id)
    evidence = rows if isinstance(rows, Mapping) else {"execution_rows": list(rows or [])}
    transaction_input = list(evidence.get("transaction_rows", []))
    service_input = list(evidence.get("service_rows", []))
    execution_rows = list(evidence.get("execution_rows", []))
    statistical_correlations = list(
        evidence.get("statistical_correlations")
        or calculate_correlations(execution_rows)
    )

    transaction_rows = _normalise_transaction_rows(variance or [], transaction_input)
    service_rows = _normalise_service_rows(variance or [], service_input, service)
    regressions = [
        row for row in transaction_rows
        if row["variance_pct"] >= DEFAULT_REGRESSION_THRESHOLD_PCT
        and _metric_group(row["metric"]) in {"LATENCY", "ERROR"}
    ]
    regressions.sort(key=lambda row: row["variance_pct"], reverse=True)

    # A primary transaction must come only from a genuine positive transaction
    # comparison. Bottleneck output cannot manufacture a transaction regression.
    primary = regressions[0] if regressions else None

    mapped_service: str | None = None
    mapping_method = "UNMAPPED"

    improvements = [
        row for row in transaction_rows
        if row["variance_pct"] < 0
        and _metric_group(row["metric"]) in {"LATENCY", "ERROR"}
    ]
    improvements.sort(key=lambda row: row["variance_pct"])
    top_improvement = improvements[0] if improvements else None

    mapping_subject = primary or top_improvement
    mapping_overview = mapping_status(row["entity"] for row in transaction_rows)
    subject_mapping = mapping_details(mapping_subject["entity"]) if mapping_subject else None
    if mapping_subject:
        mapped_service, mapping_method = map_transaction_to_service(
            mapping_subject["entity"], transaction_service_map
        )
        if subject_mapping:
            mapping_method = "EXACT_CONTRACT_MAPPING"
    mapped_rows = [
        row for row in service_rows
        if mapped_service and _normalise(row["service_name"]) == _normalise(mapped_service)
    ]

    if primary is None and top_improvement is not None:
        classification = "IMPROVEMENT_OBSERVED"
        hypothesis_category = "NOT_APPLICABLE"
        hypothesis_statement = (
            "No qualifying transaction regression was found. The strongest "
            "transaction signal is an improvement, so causal regression "
            "attribution is not applicable."
        )
    elif primary is None:
        classification = "NO_TRANSACTION_REGRESSION"
        hypothesis_category = "NOT_APPLICABLE"
        hypothesis_statement = (
            "No qualifying transaction regression was found for causal assessment."
        )
    else:
        classification, hypothesis_category, hypothesis_statement = _classification(
            primary, transaction_rows, mapped_rows, anomaly
        )

    observed_facts: list[str] = []
    supporting_signals: list[dict[str, Any]] = []
    contradicting_signals: list[dict[str, Any]] = []
    evidence_gaps: list[str] = []

    if primary:
        observed_facts.append(_format_fact(primary))
        same_transaction = [row for row in transaction_rows if row["entity"] == primary["entity"]]
        for row in sorted(same_transaction, key=lambda item: item["metric"]):
            if row is not primary and (row["entity"], row["metric"]) != (primary["entity"], primary["metric"]):
                observed_facts.append(_format_fact(row))
    else:
        if top_improvement is not None:
            observed_facts.append(_format_fact(top_improvement))
        evidence_gaps.append("No qualifying positive transaction regression was found.")

    for row in mapped_rows:
        group = _metric_group(row["metric"])
        variance_pct = _variance(row)
        current = _number(row.get("current_value"), 0.0) or 0.0
        signal = {
            "service": row["service_name"],
            "metric": row["metric"],
            "variance_pct": round(variance_pct, 3),
            "current_value": row.get("current_value"),
            "reference_value": row.get("reference_value"),
            "source": row.get("source"),
            "statement": _format_fact(row, service=True),
        }
        if not row.get("available", True):
            continue
        if group in {"LATENCY", "ERROR"} and variance_pct >= DEFAULT_REGRESSION_THRESHOLD_PCT:
            supporting_signals.append(signal)
        elif group in {"CPU", "HEAP", "GC", "THREAD"}:
            if current >= DEFAULT_RESOURCE_PRESSURE_PCT or variance_pct >= DEFAULT_STRONG_REGRESSION_PCT:
                supporting_signals.append(signal)
            else:
                signal["statement"] = (
                    f"{row['service_name']} {row['metric']} does not indicate resource pressure "
                    f"(current={current:.2f}, variance={variance_pct:.2f}%)."
                )
                contradicting_signals.append(signal)
        elif group == "LATENCY" and variance_pct <= 0:
            contradicting_signals.append(signal)

    anomaly_agreement = _anomaly_present(anomaly)
    if primary is not None and anomaly_agreement:
        supporting_signals.append(
            {
                "service": mapped_service,
                "metric": "anomaly",
                "variance_pct": None,
                "source": "aiperf_anomaly_detection",
                "statement": "Anomaly detection identified abnormal behaviour for this run.",
            }
        )
    elif primary is not None:
        contradicting_signals.append(
            {
                "service": mapped_service,
                "metric": "anomaly",
                "variance_pct": None,
                "source": "aiperf_anomaly_detection",
                "statement": "Anomaly detection did not identify abnormal behaviour for this run.",
            }
        )

    if mapping_overview.get("status") != "AVAILABLE":
        evidence_gaps.append(
            "Transaction-to-service mapping contract is unavailable: "
            + _text(mapping_overview.get("reason"), "unknown reason")
        )
    elif mapping_subject and not mapped_service:
        evidence_gaps.append(f"No mapping entry was found for {mapping_subject['entity']}.")
    elif primary and not mapped_rows:
        evidence_gaps.append(f"No service comparison or runtime evidence was found for {mapped_service}.")
    if not statistical_correlations:
        evidence_gaps.append("Insufficient aligned time-series samples for statistical correlation.")
    evidence_gaps.extend(
        [
            "Distributed trace evidence is unavailable.",
            "Database query timing evidence is unavailable.",
            "Downstream dependency latency evidence is unavailable.",
        ]
    )
    evidence_gaps.extend(evidence.get("query_errors", []))

    confidence = _confidence(
        primary=primary,
        mapped_service=mapped_service,
        mapping_method=mapping_method,
        mapped_service_rows=mapped_rows,
        supporting_count=len(supporting_signals),
        contradicting_count=len(contradicting_signals),
        anomaly_agreement=anomaly_agreement,
        statistical_correlations=statistical_correlations,
    )

    if primary is None:
        hypothesis_status = "NOT_APPLICABLE"
    elif confidence["level"] == "LOW":
        hypothesis_status = "LOW_CONFIDENCE_HYPOTHESIS"
    else:
        hypothesis_status = "HYPOTHESIS"

    business_impact = _text(
        (release or {}).get("rationale")
        or (release or {}).get("reason")
        or "Release impact must be assessed using the observed regression, scope, and evidence confidence."
    )
    systemic = primary is not None and classification in {
        "RESOURCE_SATURATION",
        "ERROR_DRIVEN_DEGRADATION",
        "THROUGHPUT_PRESSURE",
    }

    primary_output = None
    if primary:
        primary_output = {
            **primary,
            "mapped_service": mapped_service,
            "mapping_method": mapping_method,
        }

    root_text = hypothesis_statement
    result = {
        "schema_version": "aiperf-correlation.v2",
        "engine_version": ENGINE_VERSION,
        "run_id": safe_run_id,
        "generated_time": datetime.now(timezone.utc).isoformat(),
        "primary_transaction": primary_output,
        "mapping": {
            **mapping_overview,
            "subject_transaction": mapping_subject["entity"] if mapping_subject else None,
            "mapping_method": mapping_method,
            "entry_service": (subject_mapping or {}).get("entry_service"),
            "target_service": (subject_mapping or {}).get("target_service", mapped_service),
            "service_path": (subject_mapping or {}).get("service_path", []),
            "business_criticality": (subject_mapping or {}).get("business_criticality"),
            "rca_applicability": "APPLICABLE" if primary else "NOT_APPLICABLE",
        },
        "classification": classification,
        "systemic_degradation": systemic,
        "observed_facts": _unique_strings(observed_facts),
        "supporting_signals": supporting_signals,
        "contradicting_signals": contradicting_signals,
        "statistical_correlations": statistical_correlations[:10],
        # Backward-compatible alias used by existing consumers.
        "correlations": statistical_correlations[:10],
        "root_cause_hypothesis": {
            "category": hypothesis_category,
            "statement": hypothesis_statement,
            "status": hypothesis_status,
        },
        # Backward-compatible text field used by the existing findings package.
        "root_cause": root_text,
        "causal_confidence": confidence,
        "business_impact": business_impact,
        "evidence_gaps": _unique_strings(evidence_gaps),
        "release_context": dict(release or {}),
        "provenance": {
            "transaction_sources": sorted({row.get("source", "unknown") for row in transaction_rows}),
            "service_sources": sorted({row.get("source", "unknown") for row in service_rows}),
            "anomaly_source": "aiperf_anomaly_detection",
            "bottleneck_source": "aiperf_bottleneck_intelligence",
            "raw_rows_sent_to_gpt": False,
        },
    }
    return result


def persist_correlation_intelligence(
    client: Any,
    result: Mapping[str, Any],
    *,
    measurement: str = DEFAULT_MEASUREMENT,
) -> None:
    """Persist one parseable, run-scoped correlation-intelligence record."""
    run_id = _safe_run_id(result.get("run_id"))
    hypothesis = result.get("root_cause_hypothesis") or {}
    confidence = result.get("causal_confidence") or {}
    primary = result.get("primary_transaction") or {}
    mapping = dict(result.get("mapping") or {})

    point = {
        "measurement": measurement,
        "tags": {
            "run_id": run_id,
            "classification": _text(result.get("classification"), "UNKNOWN"),
            "confidence_level": _text(confidence.get("level"), "LOW"),
            "mapped_service": _text(primary.get("mapped_service") or mapping.get("target_service"), "UNMAPPED"),
            "mapping_status": _text(mapping.get("status"), "UNAVAILABLE"),
        },
        "fields": {
            "schema_version": _text(result.get("schema_version"), "aiperf-correlation.v2"),
            "engine_version": _text(result.get("engine_version"), ENGINE_VERSION),
            "primary_transaction": _text(primary.get("entity"), "UNAVAILABLE"),
            "primary_metric": _text(primary.get("metric"), "UNAVAILABLE"),
            "primary_variance_pct": float(_number(primary.get("variance_pct"), 0.0) or 0.0),
            "root_cause_category": _text(hypothesis.get("category"), "INSUFFICIENT_EVIDENCE"),
            "root_cause": _text(result.get("root_cause"), "Insufficient correlated signals."),
            "hypothesis_status": _text(hypothesis.get("status"), "LOW_CONFIDENCE_HYPOTHESIS"),
            "confidence_score": float(_number(confidence.get("score"), 0.0) or 0.0),
            "business_impact": _text(result.get("business_impact")),
            "systemic_degradation": bool(result.get("systemic_degradation", False)),
            "observed_facts_json": _json_dumps(result.get("observed_facts", [])),
            "supporting_signals_json": _json_dumps(result.get("supporting_signals", [])),
            "contradicting_signals_json": _json_dumps(result.get("contradicting_signals", [])),
            "correlations_json": _json_dumps(result.get("statistical_correlations", [])),
            # Legacy field retained, now valid JSON instead of Python repr.
            "correlations": _json_dumps(result.get("statistical_correlations", [])),
            "confidence_basis_json": _json_dumps(confidence.get("basis", [])),
            "evidence_gaps_json": _json_dumps(result.get("evidence_gaps", [])),
            "mapping_source": _text(mapping.get("source")),
            "mapping_method": _text(mapping.get("mapping_method"), "UNMAPPED"),
            "mapping_target_service": _text(mapping.get("target_service")),
            "mapping_business_criticality": _text(mapping.get("business_criticality"), "UNSPECIFIED"),
            "mapping_rca_applicability": _text(mapping.get("rca_applicability"), "NOT_APPLICABLE"),
            "mapping_json": _json_dumps(mapping),
            "result_json": _json_dumps(dict(result)),
        },
    }
    written = client.write_points([point])
    if written is False:
        raise RuntimeError("InfluxDB rejected the correlation intelligence record")


def summarize_correlations(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return bounded evidence suitable for the AiPERF findings package."""
    confidence = result.get("causal_confidence") or {}
    hypothesis = result.get("root_cause_hypothesis") or {}
    primary = result.get("primary_transaction") or {}
    supporting = list(result.get("supporting_signals") or [])
    contradicting = list(result.get("contradicting_signals") or [])
    statistical = list(
        result.get("statistical_correlations")
        or result.get("correlations")
        or []
    )

    if primary:
        lead = (
            f"{primary.get('entity', 'UNKNOWN')} {primary.get('metric', 'metric')} "
            f"regressed by {float(_number(primary.get('variance_pct'), 0.0) or 0.0):.2f}%"
        )
        if primary.get("mapped_service"):
            lead += f" and maps to {primary['mapped_service']}"
        lead += "."
    elif result.get("classification") == "IMPROVEMENT_OBSERVED":
        facts = list(result.get("observed_facts") or [])
        lead = facts[0] if facts else "A transaction improvement was observed."
    else:
        lead = "No qualifying transaction regression was available."

    summary = (
        f"{lead} Classification: {result.get('classification', 'UNKNOWN')}. "
        f"{hypothesis.get('statement', result.get('root_cause', 'Insufficient correlated signals.'))} "
        f"Causal confidence is {confidence.get('level', 'LOW')} "
        f"({float(_number(confidence.get('score'), 0.0) or 0.0):.3f})."
    )
    mapping = dict(result.get("mapping") or {})
    return {
        "schema_version": result.get("schema_version", "aiperf-correlation.v2"),
        "summary": summary,
        "primary_transaction": primary or None,
        "mapping": mapping,
        "mapping_status": mapping.get("status", "UNAVAILABLE"),
        "mapped_service": mapping.get("target_service"),
        "business_criticality": mapping.get("business_criticality"),
        "service_path": list(mapping.get("service_path") or []),
        "rca_applicability": mapping.get("rca_applicability", "NOT_APPLICABLE"),
        "classification": result.get("classification", "INSUFFICIENT_EVIDENCE"),
        "systemic_degradation": bool(result.get("systemic_degradation", False)),
        "root_cause_hypothesis": hypothesis.get(
            "statement", result.get("root_cause", "Insufficient correlated signals.")
        ),
        "root_cause_category": hypothesis.get("category", "INSUFFICIENT_EVIDENCE"),
        "hypothesis_status": hypothesis.get("status", "LOW_CONFIDENCE_HYPOTHESIS"),
        # Backward-compatible scalar confidence.
        "confidence": round(float(_number(confidence.get("score"), 0.0) or 0.0), 3),
        "confidence_level": confidence.get("level", "LOW"),
        "confidence_basis": list(confidence.get("basis") or []),
        "business_impact": result.get("business_impact", ""),
        "observed_facts": list(result.get("observed_facts") or [])[:10],
        "supporting_signals": supporting[:10],
        "contradicting_signals": contradicting[:10],
        "statistical_correlations": statistical[:5],
        "evidence_gaps": list(result.get("evidence_gaps") or [])[:10],
    }


def _latest_run_id(client: Any) -> str:
    queries = (
        'SELECT * FROM "aiperf_variance_ranking" ORDER BY time DESC LIMIT 1',
        'SELECT * FROM "aiperf_execution_history" ORDER BY time DESC LIMIT 1',
    )
    for query in queries:
        try:
            rows = _query_points(client, query)
        except Exception:
            rows = []
        if rows:
            run_id = rows[0].get("run_id") or rows[0].get("current_run_id")
            if run_id:
                return _safe_run_id(run_id)
    raise RuntimeError("Unable to determine the latest RUN_ID")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate AiPERF transaction-to-service correlation intelligence."
    )
    parser.add_argument("--run-id", default=os.getenv("RUN_ID"))
    parser.add_argument("--host", default=os.getenv("INFLUX_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("INFLUX_PORT", "8086")))
    parser.add_argument("--database", default=os.getenv("INFLUX_DATABASE", "jmeter"))
    parser.add_argument("--measurement", default=DEFAULT_MEASUREMENT)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    try:
        from influxdb import InfluxDBClient
    except ImportError as exc:
        raise RuntimeError("Install the 'influxdb' package to run this script") from exc

    client = InfluxDBClient(host=args.host, port=args.port, database=args.database)
    try:
        run_id = _safe_run_id(args.run_id) if args.run_id else _latest_run_id(client)
        evidence = query_correlations(client, run_id)

        # Use comparison rows directly. The findings package can additionally
        # pass anomaly, bottleneck, release, and service-health summaries.
        result = analyze_correlation(run_id, rows=evidence)
        if not args.no_write:
            persist_correlation_intelligence(
                client, result, measurement=args.measurement
            )
        print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate direction-aware, run-scoped AiPERF RCA evidence."""

from __future__ import annotations

import math
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from influxdb import InfluxDBClient


INFLUX_HOST = os.getenv("INFLUX_HOST", "localhost")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DATABASE", "jmeter")
INFLUX_USER = os.getenv("INFLUX_USER")
INFLUX_PASSWORD = os.getenv("INFLUX_PASSWORD")
INFLUX_TIMEOUT_SECONDS = int(os.getenv("INFLUX_TIMEOUT_SECONDS", "30"))

RUN_COMPARISON_MEASUREMENT = "aiperf_run_comparison"
TRANSACTION_COMPARISON_MEASUREMENT = "aiperf_transaction_comparison"
SERVICE_COMPARISON_MEASUREMENT = "aiperf_service_comparison"
AI_INSIGHTS_MEASUREMENT = "aiperf_ai_insights"

TRANSACTION_REGRESSION_EPSILON = float(
    os.getenv("AIPERF_TRANSACTION_REGRESSION_EPSILON", "0.0")
)

TRANSACTION_SERVICE_MAP = {
    "GET Users API": "user-service",
    "GET User By ID API": "user-service",
    "GET Products API": "product-service",
    "GET Product By ID API": "product-service",
    "GET Orders API": "order-service",
    "GET Order By ID API": "order-service",
}

SERVICE_METRICS = (
    ("avg_rt", "avg_rt_variance_pct", "avg_rt_comparable"),
    ("heap_pct", "heap_pct_variance_pct", "heap_pct_comparable"),
    ("gc_overhead", "gc_variance_pct", "gc_comparable"),
)


def finite_number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def boolean_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "valid"}
    return finite_number(value) != 0.0


def safe_run_id(value: Any) -> str:
    run_id = str(value or "").strip()
    if not run_id:
        raise ValueError(
            "RUN_ID must be provided as the first CLI argument or environment variable"
        )
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", run_id):
        raise ValueError("RUN_ID contains unsupported characters")
    return run_id


def create_client() -> InfluxDBClient:
    return InfluxDBClient(
        host=INFLUX_HOST,
        port=INFLUX_PORT,
        username=INFLUX_USER,
        password=INFLUX_PASSWORD,
        database=INFLUX_DB,
        timeout=INFLUX_TIMEOUT_SECONDS,
    )


def query_points(client: InfluxDBClient, query: str) -> List[Dict[str, Any]]:
    return [dict(row) for row in client.query(query).get_points()]


def validate_run_comparison(client: InfluxDBClient, run_id: str) -> None:
    query = f'''
SELECT *
FROM "{RUN_COMPARISON_MEASUREMENT}"
WHERE "current_run_id" = '{run_id}'
ORDER BY time DESC
LIMIT 1
'''
    if not query_points(client, query):
        raise RuntimeError(f"No run comparison data found for {run_id}")


def get_transaction_rows(
    client: InfluxDBClient,
    run_id: str,
) -> List[Dict[str, Any]]:
    query = f'''
SELECT *
FROM "{TRANSACTION_COMPARISON_MEASUREMENT}"
WHERE "current_run_id" = '{run_id}'
'''
    rows = query_points(client, query)
    if not rows:
        raise RuntimeError(f"No transaction comparison data found for {run_id}")
    return rows


def get_service_rows(
    client: InfluxDBClient,
    run_id: str,
) -> List[Dict[str, Any]]:
    query = f'''
SELECT *
FROM "{SERVICE_COMPARISON_MEASUREMENT}"
WHERE "current_run_id" = '{run_id}'
'''
    return query_points(client, query)


def select_top_transaction_regression(
    rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        variance = finite_number(row.get("variance_pct"))
        transaction = str(row.get("transaction") or "").strip()
        metric = str(row.get("metric") or "").strip()
        if not transaction or not metric:
            continue
        if variance <= TRANSACTION_REGRESSION_EPSILON:
            continue
        candidate = dict(row)
        candidate["variance_pct"] = variance
        candidates.append(candidate)
    return max(candidates, key=lambda row: row["variance_pct"]) if candidates else None


def select_top_transaction_improvement(
    rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        variance = finite_number(row.get("variance_pct"))
        transaction = str(row.get("transaction") or "").strip()
        metric = str(row.get("metric") or "").strip()
        if not transaction or not metric or variance >= 0:
            continue
        candidate = dict(row)
        candidate["variance_pct"] = variance
        candidates.append(candidate)
    return min(candidates, key=lambda row: row["variance_pct"]) if candidates else None


def select_mapped_service_signal(
    service_rows: List[Dict[str, Any]],
    mapped_service: str,
) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for row in service_rows:
        service_name = str(row.get("service_name") or "").strip()
        if service_name != mapped_service:
            continue
        for metric, variance_field, comparable_field in SERVICE_METRICS:
            if comparable_field in row and not boolean_value(row.get(comparable_field)):
                continue
            variance = finite_number(row.get(variance_field))
            if variance <= 0:
                continue
            candidates.append(
                {
                    "service_name": service_name,
                    "metric": metric,
                    "variance_pct": variance,
                }
            )
    return max(candidates, key=lambda item: item["variance_pct"]) if candidates else None


def build_regression_rca(
    transaction: Dict[str, Any],
    service_signal: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    transaction_name = str(transaction.get("transaction"))
    transaction_metric = str(transaction.get("metric"))
    transaction_variance = finite_number(transaction.get("variance_pct"))
    mapped_service = TRANSACTION_SERVICE_MAP.get(transaction_name)

    if mapped_service and service_signal:
        status = "CORRELATED_DEGRADATION_OBSERVED"
        service_name = service_signal["service_name"]
        service_metric = service_signal["metric"]
        service_variance = finite_number(service_signal["variance_pct"])
        root_cause_status = "HYPOTHESIS"
        text = (
            "RCA Status: CORRELATED_DEGRADATION_OBSERVED\n"
            "Performance Direction: REGRESSION\n"
            f"Top Transaction Regression: {transaction_name} "
            f"{transaction_metric} +{transaction_variance:.2f}%\n"
            f"Mapped Service Signal: {service_name} "
            f"{service_metric} +{service_variance:.2f}%\n"
            "Root Cause Status: HYPOTHESIS\n"
            "Assessment: The transaction and mapped service contain aligned "
            "degrading signals. Additional trace, database, and dependency "
            "evidence is required before confirming causality.\n"
            f"Recommendation: Investigate {transaction_name} and {service_name} "
            "using aligned traces, database timing, and downstream dependency latency."
        )
    else:
        status = "UNEXPLAINED_TRANSACTION_REGRESSION"
        service_name = mapped_service or "UNMAPPED"
        service_metric = "NONE"
        service_variance = 0.0
        root_cause_status = "INSUFFICIENT_EVIDENCE"
        text = (
            "RCA Status: UNEXPLAINED_TRANSACTION_REGRESSION\n"
            "Performance Direction: REGRESSION\n"
            f"Top Transaction Regression: {transaction_name} "
            f"{transaction_metric} +{transaction_variance:.2f}%\n"
            f"Mapped Service: {service_name}\n"
            "Mapped Service Degradation: NOT_OBSERVED\n"
            "Root Cause Status: INSUFFICIENT_EVIDENCE\n"
            "Assessment: A transaction regression exists, but no aligned positive "
            "service degradation was found. No service is claimed as the root cause.\n"
            "Recommendation: Collect distributed traces, database timing, and "
            "downstream dependency latency for the affected transaction."
        )

    fields = {
        "rca_status": status,
        "performance_direction": "REGRESSION",
        "root_cause_status": root_cause_status,
        "transaction_name": transaction_name,
        "transaction_metric": transaction_metric,
        "transaction_variance_pct": float(transaction_variance),
        "service_name": service_name,
        "service_metric": service_metric,
        "service_variance_pct": float(service_variance),
        "rca_text": text,
    }
    return text, fields


def build_no_regression_rca(
    improvement: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    if improvement:
        transaction_name = str(improvement.get("transaction"))
        transaction_metric = str(improvement.get("metric"))
        transaction_variance = finite_number(improvement.get("variance_pct"))
        direction = "IMPROVEMENT_OBSERVED"
        text = (
            "RCA Status: NO_TRANSACTION_REGRESSION\n"
            "Performance Direction: IMPROVEMENT_OBSERVED\n"
            f"Top Transaction Improvement: {transaction_name} "
            f"{transaction_metric} {transaction_variance:.2f}%\n"
            "Root Cause Status: NOT_APPLICABLE\n"
            "Assessment: No positive transaction regression was found. "
            "RCA attribution is not applicable for this run.\n"
            "Recommendation: Continue controlled-run observation and validate "
            "whether the improvement is repeatable."
        )
    else:
        transaction_name = "NONE"
        transaction_metric = "NONE"
        transaction_variance = 0.0
        direction = "NO_CHANGE"
        text = (
            "RCA Status: NO_TRANSACTION_REGRESSION\n"
            "Performance Direction: NO_CHANGE\n"
            "Top Transaction Improvement: NONE\n"
            "Root Cause Status: NOT_APPLICABLE\n"
            "Assessment: No positive or negative transaction variance was found.\n"
            "Recommendation: Continue controlled-run observation."
        )

    fields = {
        "rca_status": "NO_TRANSACTION_REGRESSION",
        "performance_direction": direction,
        "root_cause_status": "NOT_APPLICABLE",
        "transaction_name": transaction_name,
        "transaction_metric": transaction_metric,
        "transaction_variance_pct": float(transaction_variance),
        "service_name": "NONE",
        "service_metric": "NONE",
        "service_variance_pct": 0.0,
        "rca_text": text,
    }
    return text, fields


def persist_rca(
    client: InfluxDBClient,
    run_id: str,
    fields: Dict[str, Any],
) -> None:
    point = {
        "measurement": AI_INSIGHTS_MEASUREMENT,
        "tags": {
            "run_id": run_id,
            "insight_type": "rca",
            "rca_status": str(fields["rca_status"]),
            "performance_direction": str(fields["performance_direction"]),
        },
        "fields": fields,
    }
    if client.write_points([point]) is False:
        raise RuntimeError("InfluxDB rejected the RCA insight")


def main() -> int:
    run_id = safe_run_id(
        sys.argv[1] if len(sys.argv) > 1 else os.getenv("RUN_ID")
    )

    print("\n===================================")
    print("AiPERF RCA ENGINE")
    print("===================================")
    print(f"\nCurrent Run : {run_id}")

    client = create_client()
    try:
        client.ping()
        validate_run_comparison(client, run_id)
        transaction_rows = get_transaction_rows(client, run_id)
        service_rows = get_service_rows(client, run_id)

        top_regression = select_top_transaction_regression(transaction_rows)
        if top_regression is None:
            top_improvement = select_top_transaction_improvement(transaction_rows)
            rca_text, fields = build_no_regression_rca(top_improvement)
        else:
            transaction_name = str(top_regression.get("transaction"))
            mapped_service = TRANSACTION_SERVICE_MAP.get(transaction_name)
            service_signal = (
                select_mapped_service_signal(service_rows, mapped_service)
                if mapped_service
                else None
            )
            rca_text, fields = build_regression_rca(
                top_regression,
                service_signal,
            )

        print("\n===================================")
        print("RCA GENERATED")
        print("===================================")
        print(rca_text)

        persist_rca(client, run_id, fields)

        print("\n===================================")
        print("RCA WRITTEN TO aiperf_ai_insights")
        print("===================================")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("\n===================================")
        print("RCA ENGINE FAILED")
        print("===================================")
        print(f"Error Type: {type(exc).__name__}")
        print(f"Error: {exc}")
        raise SystemExit(1)

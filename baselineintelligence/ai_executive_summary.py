import math
import os
import sys
from typing import Any, Dict, List, Optional

from influxdb import InfluxDBClient


INFLUX_HOST = os.getenv("INFLUX_HOST", "localhost")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DB", "jmeter")
INFLUX_USER = os.getenv("INFLUX_USER")
INFLUX_PASSWORD = os.getenv("INFLUX_PASSWORD")
INFLUX_TIMEOUT_SECONDS = int(os.getenv("INFLUX_TIMEOUT_SECONDS", "30"))

VARIANCE_MEASUREMENT = "aiperf_variance_ranking"
SERVICE_COMPARISON_MEASUREMENT = "aiperf_service_comparison"
AI_INSIGHTS_MEASUREMENT = "aiperf_ai_insights"


SERVICE_METRICS = (
    {
        "metric": "avg_rt",
        "variance_field": "avg_rt_variance_pct",
        "comparable_field": "avg_rt_comparable",
    },
    {
        "metric": "heap_pct",
        "variance_field": "heap_pct_variance_pct",
        "comparable_field": "heap_pct_comparable",
    },
    {
        "metric": "gc_overhead",
        "variance_field": "gc_variance_pct",
        "comparable_field": "gc_comparable",
    },
    {
        "metric": "request_count",
        "variance_field": "request_count_variance_pct",
        "comparable_field": "request_count_comparable",
    },
)


def required_run_id() -> str:
    run_id = os.getenv("RUN_ID")
    if run_id is None or not run_id.strip():
        raise RuntimeError("RUN_ID environment variable not found or empty")
    return run_id.strip()


def escape_influxql_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def finite_number(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def is_comparable(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "valid"}
    number = finite_number(value)
    return number is not None and number != 0.0


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
    return list(client.query(query).get_points())


def get_top_transaction_regression(
    client: InfluxDBClient,
    run_id: str,
) -> Optional[Dict[str, Any]]:
    safe_run_id = escape_influxql_string(run_id)
    query = f'''
SELECT "entity_name", "metric", "variance_pct"
FROM "{VARIANCE_MEASUREMENT}"
WHERE "run_id" = '{safe_run_id}'
  AND "entity_type" = 'transaction'
'''
    candidates: List[Dict[str, Any]] = []
    for row in query_points(client, query):
        variance = finite_number(row.get("variance_pct"))
        if variance is None or variance <= 0:
            continue
        entity_name = str(row.get("entity_name") or "").strip()
        metric = str(row.get("metric") or "").strip()
        if not entity_name or not metric:
            continue
        candidates.append(
            {
                "entity_name": entity_name,
                "metric": metric,
                "variance_pct": variance,
            }
        )
    return max(candidates, key=lambda item: item["variance_pct"]) if candidates else None


def get_top_service_regression(
    client: InfluxDBClient,
    run_id: str,
) -> Optional[Dict[str, Any]]:
    safe_run_id = escape_influxql_string(run_id)
    query = f'''
SELECT *
FROM "{SERVICE_COMPARISON_MEASUREMENT}"
WHERE "current_run_id" = '{safe_run_id}'
'''
    candidates: List[Dict[str, Any]] = []
    for row in query_points(client, query):
        service_name = str(row.get("service_name") or "").strip()
        if not service_name:
            continue
        for definition in SERVICE_METRICS:
            comparable_field = definition["comparable_field"]
            if comparable_field in row and not is_comparable(row.get(comparable_field)):
                continue
            variance = finite_number(row.get(definition["variance_field"]))
            if variance is None or variance <= 0:
                continue
            candidates.append(
                {
                    "entity_name": service_name,
                    "metric": definition["metric"],
                    "variance_pct": variance,
                }
            )
    return max(candidates, key=lambda item: item["variance_pct"]) if candidates else None


def build_insights(
    transaction: Optional[Dict[str, Any]],
    service: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    insights: List[Dict[str, str]] = []
    if transaction is not None:
        insights.append(
            {
                "category": "top_transaction_regression",
                "text": (
                    "Top Transaction Regression: "
                    f"{transaction['entity_name']} "
                    f"{transaction['metric']} "
                    f"{transaction['variance_pct']:.2f}%"
                ),
            }
        )
    if service is not None:
        insights.append(
            {
                "category": "top_service_regression",
                "text": (
                    "Top Service Regression: "
                    f"{service['entity_name']} "
                    f"{service['metric']} "
                    f"{service['variance_pct']:.2f}%"
                ),
            }
        )
    return insights


def write_insights(
    client: InfluxDBClient,
    run_id: str,
    insights: List[Dict[str, str]],
) -> None:
    points = [
        {
            "measurement": AI_INSIGHTS_MEASUREMENT,
            "tags": {
                "run_id": run_id,
                "insight_type": "executive_summary",
                "summary_category": insight["category"],
            },
            "fields": {
                "insight_text": insight["text"],
            },
        }
        for insight in insights
    ]
    if points and client.write_points(points) is False:
        raise RuntimeError("InfluxDB rejected executive summary points")


def main() -> int:
    run_id = required_run_id()
    client = create_client()
    try:
        client.ping()
        transaction = get_top_transaction_regression(client, run_id)
        service = get_top_service_regression(client, run_id)
        insights = build_insights(transaction, service)
        if not insights:
            print(f"Executive Summary Status: NO_REGRESSIONS for {run_id}")
            print("No positive comparable transaction or service regressions found")
            return 0
        write_insights(client, run_id, insights)
        print(f"Executive Summary Created for {run_id}")
        for insight in insights:
            print(insight["text"])
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("Executive Summary Generation Failed")
        print(f"Error Type: {type(exc).__name__}")
        print(f"Error: {exc}")
        sys.exit(1)

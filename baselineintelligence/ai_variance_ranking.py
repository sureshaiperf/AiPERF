"""Build run-scoped AiPERF variance ranking from authoritative comparisons."""

from __future__ import annotations

import math
import os
import re
import sys
from typing import Any, Dict, List

from influxdb import InfluxDBClient


INFLUX_HOST = os.getenv("INFLUX_HOST", "localhost")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DATABASE", os.getenv("INFLUX_DB", "jmeter"))
INFLUX_USER = os.getenv("INFLUX_USER")
INFLUX_PASSWORD = os.getenv("INFLUX_PASSWORD")
INFLUX_TIMEOUT_SECONDS = int(os.getenv("INFLUX_TIMEOUT_SECONDS", "30"))

TRANSACTION_COMPARISON_MEASUREMENT = "aiperf_transaction_comparison"
SERVICE_COMPARISON_MEASUREMENT = "aiperf_service_comparison"
RANKING_MEASUREMENT = "aiperf_variance_ranking"

TRANSACTION_EXCLUSIONS = {"samples", "errors"}

SERVICE_METRICS = (
    {
        "metric": "avg_rt",
        "baseline_field": "avg_rt_baseline",
        "current_field": "avg_rt_current",
        "variance_field": "avg_rt_variance_pct",
        "comparable_field": "avg_rt_comparable",
    },
    {
        "metric": "heap_pct",
        "baseline_field": "heap_pct_baseline",
        "current_field": "heap_pct_current",
        "variance_field": "heap_pct_variance_pct",
        "comparable_field": "heap_pct_comparable",
    },
    {
        "metric": "gc_overhead",
        "baseline_field": "gc_baseline",
        "current_field": "gc_current",
        "variance_field": "gc_variance_pct",
        "comparable_field": "gc_comparable",
    },
)


def safe_run_id(value: Any) -> str:
    run_id = str(value or "").strip()
    if not run_id:
        raise ValueError(
            "RUN_ID must be provided as the first CLI argument or environment variable"
        )
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", run_id):
        raise ValueError("RUN_ID contains unsupported characters")
    return run_id


def finite_number(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
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
    return [dict(row) for row in client.query(query).get_points()]


def load_transaction_rankings(
    client: InfluxDBClient,
    run_id: str,
) -> List[Dict[str, Any]]:
    query = f'''
SELECT *
FROM "{TRANSACTION_COMPARISON_MEASUREMENT}"
WHERE "current_run_id" = '{run_id}'
'''
    rankings: List[Dict[str, Any]] = []
    for row in query_points(client, query):
        entity_name = str(
            row.get("transaction") or row.get("entity_name") or ""
        ).strip()
        metric = str(row.get("metric") or "").strip()
        variance = finite_number(row.get("variance_pct"))

        if not entity_name or not metric or variance is None:
            continue
        if metric.lower() in TRANSACTION_EXCLUSIONS:
            continue

        rankings.append(
            {
                "entity_type": "transaction",
                "entity_name": entity_name,
                "metric": metric,
                "variance_pct": variance,
                "current_value": finite_number(row.get("current_value")),
                "previous_value": finite_number(
                    row.get("previous_value", row.get("baseline_value"))
                ),
                "comparison_source": TRANSACTION_COMPARISON_MEASUREMENT,
                "comparison_run_id": str(
                    row.get("comparison_run_id")
                    or row.get("similar_run_id")
                    or ""
                ),
            }
        )
    return rankings


def load_service_rankings(
    client: InfluxDBClient,
    run_id: str,
) -> List[Dict[str, Any]]:
    query = f'''
SELECT *
FROM "{SERVICE_COMPARISON_MEASUREMENT}"
WHERE "current_run_id" = '{run_id}'
'''
    rankings: List[Dict[str, Any]] = []

    for row in query_points(client, query):
        service_name = str(row.get("service_name") or "").strip()
        if not service_name:
            continue

        for definition in SERVICE_METRICS:
            comparable_field = definition["comparable_field"]
            if comparable_field in row and not is_comparable(row.get(comparable_field)):
                continue

            variance = finite_number(row.get(definition["variance_field"]))
            if variance is None:
                continue

            rankings.append(
                {
                    "entity_type": "service",
                    "entity_name": service_name,
                    "metric": definition["metric"],
                    "variance_pct": variance,
                    "current_value": finite_number(
                        row.get(definition["current_field"])
                    ),
                    "previous_value": finite_number(
                        row.get(definition["baseline_field"])
                    ),
                    "comparison_source": SERVICE_COMPARISON_MEASUREMENT,
                    "comparison_run_id": str(
                        row.get("similar_run_id")
                        or row.get("comparison_run_id")
                        or ""
                    ),
                }
            )

    return rankings


def assign_ranks(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records.sort(
        key=lambda item: abs(float(item["variance_pct"])),
        reverse=True,
    )
    for index, record in enumerate(records, start=1):
        record["rank"] = index
        variance = float(record["variance_pct"])
        record["direction"] = (
            "REGRESSION"
            if variance > 0
            else "IMPROVEMENT"
            if variance < 0
            else "NO_CHANGE"
        )
    return records


def replace_run_rankings(
    client: InfluxDBClient,
    run_id: str,
    records: List[Dict[str, Any]],
) -> int:
    client.query(
        f'''DROP SERIES FROM "{RANKING_MEASUREMENT}" WHERE "run_id"='{run_id}' '''
    )

    points = []
    for record in records:
        fields: Dict[str, Any] = {
            "variance_pct": float(record["variance_pct"]),
            "rank": int(record["rank"]),
            "direction": str(record["direction"]),
            "comparison_source": str(record["comparison_source"]),
        }
        if record.get("current_value") is not None:
            fields["current_value"] = float(record["current_value"])
        if record.get("previous_value") is not None:
            fields["previous_value"] = float(record["previous_value"])
        if record.get("comparison_run_id"):
            fields["comparison_run_id"] = str(record["comparison_run_id"])

        points.append(
            {
                "measurement": RANKING_MEASUREMENT,
                "tags": {
                    "run_id": run_id,
                    "entity_type": str(record["entity_type"]),
                    "entity_name": str(record["entity_name"]),
                    "metric": str(record["metric"]),
                },
                "fields": fields,
            }
        )

    if points and client.write_points(points) is False:
        raise RuntimeError("InfluxDB rejected variance-ranking records")
    return len(points)


def print_rankings(run_id: str, records: List[Dict[str, Any]]) -> None:
    print("\n===================================")
    print("TOP 10 VARIANCES")
    print("===================================\n")

    for row in records[:10]:
        print(
            f"Rank {row['rank']} | "
            f"{row['entity_type']} | "
            f"{row['entity_name']} | "
            f"{row['metric']} | "
            f"{row['variance_pct']:.2f}% | "
            f"{row['direction']}"
        )

    print("\nTOP REGRESSIONS")
    regressions = [row for row in records if row["variance_pct"] > 0]
    regressions.sort(key=lambda row: row["variance_pct"], reverse=True)
    if not regressions:
        print("None")
    for row in regressions[:5]:
        print(
            f"{row['entity_type']} | {row['entity_name']} | "
            f"{row['metric']} | {row['variance_pct']:.2f}%"
        )

    print("\nTOP IMPROVEMENTS")
    improvements = [row for row in records if row["variance_pct"] < 0]
    improvements.sort(key=lambda row: row["variance_pct"])
    if not improvements:
        print("None")
    for row in improvements[:5]:
        print(
            f"{row['entity_type']} | {row['entity_name']} | "
            f"{row['metric']} | {row['variance_pct']:.2f}%"
        )

    print("\n===================================")
    print("VARIANCE RANKING COMPLETED")
    print("===================================")
    print(f"Run ID          : {run_id}")
    print(f"Records Written : {len(records)}")
    print(f"Measurement     : {RANKING_MEASUREMENT}")
    print("Service Source  : aiperf_service_comparison")
    print("Request Count   : EXCLUDED_FROM_REGRESSION_RANKING")


def main() -> int:
    run_id = safe_run_id(
        sys.argv[1] if len(sys.argv) > 1 else os.getenv("RUN_ID")
    )

    print("\n===================================")
    print("AiPERF VARIANCE RANKING")
    print("===================================\n")
    print(f"Latest Run : {run_id}")

    client = create_client()
    try:
        client.ping()
        transaction_records = load_transaction_rankings(client, run_id)
        service_records = load_service_rankings(client, run_id)
        records = assign_ranks(transaction_records + service_records)

        if not records:
            print(f"No comparison data found for RUN_ID={run_id}.")
            return 0

        replace_run_rankings(client, run_id, records)
        print_rankings(run_id, records)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("\n===================================")
        print("VARIANCE RANKING FAILED")
        print("===================================")
        print(f"Error Type: {type(exc).__name__}")
        print(f"Error: {exc}")
        raise SystemExit(1)

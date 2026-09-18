import math
import os
import sys
from typing import Any, Dict, List, Optional
from baseline_selection import (
    delete_current_run_series,
    validate_single_comparison_target,
)

from influxdb import InfluxDBClient


# =====================================================
# CONFIGURATION
# =====================================================

INFLUX_HOST = os.getenv("INFLUX_HOST", "localhost")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DB", "jmeter")
INFLUX_USER = os.getenv("INFLUX_USER")
INFLUX_PASSWORD = os.getenv("INFLUX_PASSWORD")
INFLUX_TIMEOUT_SECONDS = int(os.getenv("INFLUX_TIMEOUT_SECONDS", "30"))

RUN_COMPARISON_MEASUREMENT = "aiperf_run_comparison"
SERVICE_METRICS_MEASUREMENT = "aiperf_service_metrics"
SERVICE_COMPARISON_MEASUREMENT = "aiperf_service_comparison"

SERVICES = (
    "gateway",
    "user-service",
    "product-service",
    "order-service",
)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(
            f"Required environment variable is missing or empty: {name}"
        )
    return value.strip()


def escape_influxql_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        normalized = float(value)
        if not math.isfinite(normalized):
            return default
        return normalized
    except (TypeError, ValueError, OverflowError):
        return default


def available(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "available",
            "valid",
        }
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value)


def percentage_variance(current_value: float, baseline_value: float) -> float:
    if baseline_value <= 0:
        return 0.0
    return round(
        ((current_value - baseline_value) / baseline_value) * 100.0,
        2,
    )


def create_client() -> InfluxDBClient:
    return InfluxDBClient(
        host=INFLUX_HOST,
        port=INFLUX_PORT,
        username=INFLUX_USER,
        password=INFLUX_PASSWORD,
        database=INFLUX_DB,
        timeout=INFLUX_TIMEOUT_SECONDS,
    )


def query_points(
    client: InfluxDBClient,
    query: str,
) -> List[Dict[str, Any]]:
    result = client.query(query)
    return list(result.get_points())


def find_run_comparison(
    client: InfluxDBClient,
    current_run_id: str,
) -> Optional[Dict[str, Any]]:
    escaped_run_id = escape_influxql_string(current_run_id)
    query = f'''\nSELECT *\nFROM "{RUN_COMPARISON_MEASUREMENT}"\nWHERE "current_run_id" = '{escaped_run_id}'\nORDER BY time DESC\nLIMIT 1\n'''
    rows = query_points(client, query)
    return rows[0] if rows else None


def get_comparison_run_id(
    comparison_row: Dict[str, Any],
) -> Optional[str]:
    value = (
        comparison_row.get("comparison_run_id")
        or comparison_row.get("similar_run_id")
    )
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def query_latest_service_metric(
    client: InfluxDBClient,
    run_id: str,
    service_name: str,
) -> Optional[Dict[str, Any]]:
    escaped_run_id = escape_influxql_string(run_id)
    escaped_service_name = escape_influxql_string(service_name)
    query = f'''\nSELECT *\nFROM "{SERVICE_METRICS_MEASUREMENT}"\nWHERE "run_id" = '{escaped_run_id}'\n  AND "service_name" = '{escaped_service_name}'\nORDER BY time DESC\nLIMIT 1\n'''
    rows = query_points(client, query)
    return rows[0] if rows else None


def build_service_comparison(
    current_run_id: str,
    comparison_run_id: str,
    service_name: str,
    similarity_score: float,
    current: Dict[str, Any],
    baseline: Dict[str, Any],
) -> Dict[str, Any]:
    request_current = number(current.get("request_count"))
    request_baseline = number(baseline.get("request_count"))

    request_count_comparable = int(
        available(current.get("request_count_available"))
        and available(baseline.get("request_count_available"))
        and request_baseline > 0
    )
    request_variance = (
        percentage_variance(request_current, request_baseline)
        if request_count_comparable
        else 0.0
    )

    response_time_current = number(current.get("avg_response_time_ms"))
    response_time_baseline = number(baseline.get("avg_response_time_ms"))
    response_time_comparable = int(response_time_baseline > 0)
    response_time_variance = (
        percentage_variance(response_time_current, response_time_baseline)
        if response_time_comparable
        else 0.0
    )

    heap_current = number(current.get("heap_pct"))
    heap_baseline = number(baseline.get("heap_pct"))
    heap_comparable = int(heap_baseline > 0)
    heap_variance = (
        percentage_variance(heap_current, heap_baseline)
        if heap_comparable
        else 0.0
    )

    gc_current = number(current.get("gc_overhead"))
    gc_baseline = number(baseline.get("gc_overhead"))
    gc_comparable = int(gc_baseline > 0)
    gc_variance = (
        percentage_variance(gc_current, gc_baseline)
        if gc_comparable
        else 0.0
    )

    print(
        f"{service_name} | "
        f"request_comparable={request_count_comparable} | "
        f"RT={response_time_variance:.2f}% | "
        f"Heap={heap_variance:.2f}% | "
        f"GC={gc_variance:.2f}%"
    )

    return {
        "measurement": SERVICE_COMPARISON_MEASUREMENT,
        "tags": {
            "current_run_id": current_run_id,
            "similar_run_id": comparison_run_id,
            "service_name": service_name,
        },
        "fields": {
            "similarity_score": float(similarity_score),
            "request_count_baseline": float(request_baseline),
            "request_count_current": float(request_current),
            "request_count_variance_pct": float(request_variance),
            "request_count_comparable": int(request_count_comparable),
            "avg_rt_baseline": float(response_time_baseline),
            "avg_rt_current": float(response_time_current),
            "avg_rt_variance_pct": float(response_time_variance),
            "avg_rt_comparable": int(response_time_comparable),
            "active_requests_baseline": number(
                baseline.get("active_requests")
            ),
            "active_requests_current": number(
                current.get("active_requests")
            ),
            "executor_active_baseline": number(
                baseline.get("executor_active")
            ),
            "executor_active_current": number(
                current.get("executor_active")
            ),
            "heap_pct_baseline": float(heap_baseline),
            "heap_pct_current": float(heap_current),
            "heap_pct_variance_pct": float(heap_variance),
            "heap_pct_comparable": int(heap_comparable),
            "gc_baseline": float(gc_baseline),
            "gc_current": float(gc_current),
            "gc_variance_pct": float(gc_variance),
            "gc_comparable": int(gc_comparable),
        },
    }


def main() -> int:
    current_run_id = required_env("RUN_ID")

    print("=" * 70)
    print("AiPERF Service Comparison Writer")
    print("=" * 70)
    print(f"Current Run : {current_run_id}")
    print(f"InfluxDB    : {INFLUX_HOST}:{INFLUX_PORT}/{INFLUX_DB}")

    client = create_client()

    try:
        client.ping()

        comparison_row = find_run_comparison(client, current_run_id)
        if comparison_row is None:
            print()
            print("Comparison Status : BASELINE_UNAVAILABLE")
            print(
                "Reason            : No run comparison exists for "
                f"{current_run_id}"
            )
            print(
                "Action            : Skipping service comparison "
                "during bootstrap execution."
            )
            print(
                "Pipeline Status    : SUCCESS "
                "(valid first-run condition)"
            )
            return 0

        comparison_run_id = get_comparison_run_id(comparison_row)
        if not comparison_run_id:
            print()
            print("Comparison Status : BASELINE_UNAVAILABLE")
            print(
                "Reason            : Run comparison record has no "
                "comparison run identifier."
            )
            print(
                "Action            : Skipping service comparison "
                "without failing the pipeline."
            )
            print(
                "Pipeline Status    : SUCCESS "
                "(comparison target unavailable)"
            )
            return 0

        similarity_score = number(comparison_row.get("similarity_score"))

        print(f"Compared Run: {comparison_run_id}")
        print(f"Similarity  : {similarity_score:.2f}")

        points: List[Dict[str, Any]] = []
        skipped_services: List[str] = []

        for service_name in SERVICES:
            current = query_latest_service_metric(
                client,
                current_run_id,
                service_name,
            )
            baseline = query_latest_service_metric(
                client,
                comparison_run_id,
                service_name,
            )

            if current is None:
                print(
                    f"{service_name} | SKIPPED | "
                    "Current-run service metrics unavailable"
                )
                skipped_services.append(f"{service_name}:current-missing")
                continue

            if baseline is None:
                print(
                    f"{service_name} | SKIPPED | "
                    "Comparison-run service metrics unavailable"
                )
                skipped_services.append(f"{service_name}:baseline-missing")
                continue

            points.append(
                build_service_comparison(
                    current_run_id=current_run_id,
                    comparison_run_id=comparison_run_id,
                    service_name=service_name,
                    similarity_score=similarity_score,
                    current=current,
                    baseline=baseline,
                )
            )

        if not points:
            print()
            print("Comparison Status : NO_COMPARABLE_SERVICE_DATA")
            print(
                "Reason            : No service had both current-run "
                "and comparison-run metrics."
            )
            if skipped_services:
                print("Skipped Services  : " + ", ".join(skipped_services))
            print(
                "Pipeline Status    : SUCCESS "
                "(no service comparison generated)"
            )
            return 0

        delete_current_run_series(
    client,
    SERVICE_COMPARISON_MEASUREMENT,
    current_run_id,
)

        print(
            "Existing service-comparison series removed for "
            f"{current_run_id}"
        )

        print()
        print(f"Writing {len(points)} service comparison record(s)...")

        write_success = client.write_points(points, time_precision="s")
        if not write_success:
            raise RuntimeError(
                "InfluxDB returned an unsuccessful result while writing "
                "service comparison records."
            )

        validation = validate_single_comparison_target(
    client,
    SERVICE_COMPARISON_MEASUREMENT,
    current_run_id,
    comparison_run_id,
    expected_record_count=len(points),
)

        print()
        print("Service comparison validation passed")
        print(
            f"Validated Records : {validation['record_count']}"
        )
        print(
            f"Validated Target  : "
            f"{validation['comparison_run_id']}"
        )

        print()
        print("Service comparison written successfully")
        print(f"Records Written   : {len(points)}")
        if skipped_services:
            print("Skipped Services  : " + ", ".join(skipped_services))
        print("Comparison Status : COMPLETED")
        print("Pipeline Status   : SUCCESS")
        return 0

    finally:
        client.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print()
        print("=" * 70)
        print("SERVICE COMPARISON WRITER FAILED")
        print("=" * 70)
        print(f"Error Type : {type(exc).__name__}")
        print(f"Error      : {exc}")
        sys.exit(1)

"""Write AiPERF execution history and the version-2 execution fingerprint."""

from __future__ import annotations

import csv
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from influxdb import InfluxDBClient
from jmeter_run_metrics import read_jtl


INFLUX_HOST = os.getenv("INFLUX_HOST", "localhost")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DATABASE", os.getenv("INFLUX_DB", "jmeter"))
INFLUX_USER = os.getenv("INFLUX_USER")
INFLUX_PASSWORD = os.getenv("INFLUX_PASSWORD")
INFLUX_TIMEOUT_SECONDS = int(os.getenv("INFLUX_TIMEOUT_SECONDS", "30"))

APPLICATION = os.getenv("AIPERF_APPLICATION", "AiPERF")
ENVIRONMENT = os.getenv("AIPERF_ENVIRONMENT", "QA")
TEST_NAME = os.getenv("AIPERF_TEST_NAME", "API_Test")
BUILD_NUMBER = os.getenv("BUILD_NUMBER", "0")
JOB_NAME = os.getenv("JOB_NAME", "MANUAL")

SERVICES = ("gateway", "user-service", "product-service", "order-service")


def required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value.strip()


def safe_run_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ValueError("RUN_ID contains unsupported characters")
    return value


def finite_number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def create_client() -> InfluxDBClient:
    return InfluxDBClient(
        host=INFLUX_HOST,
        port=INFLUX_PORT,
        username=INFLUX_USER,
        password=INFLUX_PASSWORD,
        database=INFLUX_DB,
        timeout=INFLUX_TIMEOUT_SECONDS,
    )


def get_service_metrics(
    client: InfluxDBClient,
    run_id: str,
    service_name: str,
) -> Dict[str, Any]:
    query = f'''
SELECT
LAST(heap_pct) AS heap_pct,
LAST(active_requests) AS active_requests,
LAST(executor_active) AS executor_active,
LAST(avg_response_time_ms) AS avg_response_time_ms,
LAST(request_count) AS request_count,
LAST(request_count_available) AS request_count_available
FROM "aiperf_service_metrics"
WHERE "run_id"='{run_id}'
  AND "service_name"='{service_name}'
  AND "sample_phase"='after'
'''
    points = list(client.query(query).get_points())
    row = points[0] if points else {}
    return {
        "heap_pct": finite_number(row.get("heap_pct")),
        "active_requests": finite_number(row.get("active_requests")),
        "executor_active": finite_number(row.get("executor_active")),
        "avg_response_time_ms": finite_number(row.get("avg_response_time_ms")),
        "request_count": finite_number(row.get("request_count")),
        "request_count_available": int(
            finite_number(row.get("request_count_available")) != 0
        ),
    }


def detect_jtl_delimiter(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def calculate_jtl_duration_seconds(path: Path) -> Optional[float]:
    """Calculate actual JMeter test span from sample timestamps and elapsed time."""
    if not path.exists() or not path.is_file():
        return None

    delimiter = detect_jtl_delimiter(path)
    minimum_start_ms: Optional[float] = None
    maximum_end_ms: Optional[float] = None

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or "timeStamp" not in reader.fieldnames:
            return None

        for row in reader:
            start_ms = finite_number(row.get("timeStamp"), default=-1.0)
            elapsed_ms = max(0.0, finite_number(row.get("elapsed")))
            if start_ms < 0:
                continue
            end_ms = start_ms + elapsed_ms
            minimum_start_ms = (
                start_ms
                if minimum_start_ms is None
                else min(minimum_start_ms, start_ms)
            )
            maximum_end_ms = (
                end_ms
                if maximum_end_ms is None
                else max(maximum_end_ms, end_ms)
            )

    if minimum_start_ms is None or maximum_end_ms is None:
        return None

    duration_seconds = (maximum_end_ms - minimum_start_ms) / 1000.0
    return duration_seconds if duration_seconds > 0 else None


def load_execution_metrics(
    client: InfluxDBClient,
    jtl_path: Optional[str],
    run_start_epoch: int,
    run_end_epoch: int,
) -> Tuple[Dict[str, Any], str]:
    if jtl_path and Path(jtl_path).exists():
        metrics = read_jtl(jtl_path)
        if metrics.get("all"):
            return dict(metrics["all"]), "JTL"

    query = f'''
SELECT *
FROM "jmeter"
WHERE time >= {run_start_epoch}ms
  AND time <= {run_end_epoch}ms
  AND "transaction"='all'
  AND "statut"='all'
ORDER BY time DESC
LIMIT 1
'''
    points = list(client.query(query).get_points())
    if not points:
        raise RuntimeError("No JMeter execution data found")
    point = points[0]
    return {
        "avg_rt": point.get("avg", 0),
        "p90": point.get("pct90.0", 0),
        "p95": point.get("pct95.0", 0),
        "p99": point.get("pct99.0", 0),
        "errors": point.get("countError", 0),
        "samples": point.get("count", 0),
    }, "INFLUX_JMETER"


def main() -> int:
    run_id = safe_run_id(required_env("RUN_ID"))
    run_start_epoch = int(required_env("RUN_START_EPOCH"))
    run_end_epoch = int(required_env("RUN_END_EPOCH"))
    if run_end_epoch <= run_start_epoch:
        raise ValueError("RUN_END_EPOCH must be greater than RUN_START_EPOCH")

    jtl_path = os.getenv("JTL_PATH")
    pipeline_duration_seconds = (run_end_epoch - run_start_epoch) / 1000.0

    print(f"Using RUN_ID = {run_id}")
    client = create_client()
    try:
        client.ping()
        latest, metric_source = load_execution_metrics(
            client,
            jtl_path,
            run_start_epoch,
            run_end_epoch,
        )

        avg_rt = finite_number(latest.get("avg_rt"))
        p90 = finite_number(latest.get("p90"))
        p95 = finite_number(latest.get("p95"))
        p99 = finite_number(latest.get("p99"))
        errors = max(0, int(finite_number(latest.get("errors"))))
        sample_count = max(0, int(finite_number(latest.get("samples"))))

        jtl_duration_seconds = (
            calculate_jtl_duration_seconds(Path(jtl_path))
            if jtl_path
            else None
        )
        test_duration_seconds = jtl_duration_seconds or pipeline_duration_seconds
        duration_source = "JTL_SAMPLE_SPAN" if jtl_duration_seconds else "PIPELINE_WINDOW"
        throughput_rps = (
            sample_count / test_duration_seconds
            if sample_count > 0 and test_duration_seconds > 0
            else 0.0
        )
        error_rate = (errors / sample_count) * 100.0 if sample_count else 0.0
        execution_status = "FAIL" if errors > 0 else "PASS"

        services = {
            name: get_service_metrics(client, run_id, name)
            for name in SERVICES
        }

        print("Latest Metrics Retrieved")
        print(f"Avg RT              : {avg_rt}")
        print(f"P90                 : {p90}")
        print(f"P95                 : {p95}")
        print(f"P99                 : {p99}")
        print(f"Errors              : {errors}")
        print(f"Sample Count        : {sample_count}")
        print(f"Test Duration       : {test_duration_seconds:.3f} seconds")
        print(f"Duration Source     : {duration_source}")
        print(f"Throughput          : {throughput_rps:.3f} requests/second")
        print(f"Error Rate          : {error_rate:.2f}%")

        history_point = {
            "measurement": "aiperf_execution_history",
            "tags": {
                "application": APPLICATION,
                "environment": ENVIRONMENT,
                "test_name": TEST_NAME,
                "job_name": JOB_NAME,
                "throughput_semantic_version": "2",
            },
            "fields": {
                "run_id": run_id,
                "run_start_epoch": run_start_epoch,
                "run_end_epoch": run_end_epoch,
                "duration_seconds": round(pipeline_duration_seconds, 3),
                "test_duration_seconds": round(test_duration_seconds, 3),
                "duration_source": duration_source,
                "metric_source": metric_source,
                "build_number": int(BUILD_NUMBER) if BUILD_NUMBER.isdigit() else 0,
                "avg_rt": float(avg_rt),
                "p90": float(p90),
                "p95": float(p95),
                "p99": float(p99),
                "errors": int(errors),
                "sample_count": int(sample_count),
                "throughput": float(throughput_rps),
                "throughput_rps": float(throughput_rps),
                "error_rate": float(error_rate),
                "execution_status": execution_status,
            },
        }
        if client.write_points([history_point]) is False:
            raise RuntimeError("InfluxDB rejected execution history")

        fingerprint_fields: Dict[str, Any] = {
            "avg_rt": float(avg_rt),
            "p95": float(p95),
            "p99": float(p99),
            "throughput": float(throughput_rps),
            "throughput_rps": float(throughput_rps),
            "sample_count": int(sample_count),
            "test_duration_seconds": float(test_duration_seconds),
            "throughput_semantic_version": 2,
            "error_rate": float(error_rate),
        }

        service_prefixes = {
            "gateway": "gateway",
            "user-service": "user",
            "product-service": "product",
            "order-service": "order",
        }
        for service_name, prefix in service_prefixes.items():
            service = services[service_name]
            fingerprint_fields.update(
                {
                    f"{prefix}_heap_pct": float(service["heap_pct"]),
                    f"{prefix}_active_requests": float(service["active_requests"]),
                    f"{prefix}_executor_active": float(service["executor_active"]),
                    f"{prefix}_avg_response_time_ms": float(
                        service["avg_response_time_ms"]
                    ),
                    f"{prefix}_request_count": float(service["request_count"]),
                    f"{prefix}_request_count_available": int(
                        service["request_count_available"]
                    ),
                }
            )

        fingerprint_point = {
            "measurement": "aiperf_execution_fingerprint",
            "tags": {
                "run_id": run_id,
                "application": APPLICATION,
                "environment": ENVIRONMENT,
                "test_name": TEST_NAME,
                "throughput_semantic_version": "2",
            },
            "fields": fingerprint_fields,
        }
        if client.write_points([fingerprint_point]) is False:
            raise RuntimeError("InfluxDB rejected execution fingerprint")

        print("Execution History Written Successfully")
        print("=================================")
        print("Execution Fingerprint Written")
        print(f"Run ID : {run_id}")
        print("Fingerprint Semantic Version : 2")
        print("=================================")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Execution History Writer Failed")
        print(f"Error Type: {type(exc).__name__}")
        print(f"Error: {exc}")
        raise SystemExit(1)

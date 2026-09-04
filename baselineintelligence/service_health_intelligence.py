"""Persist a run-scoped service health assessment from collected metrics."""

from __future__ import annotations

import os
from typing import Any

from influxdb import InfluxDBClient


def _number(value: Any) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0


def assess_service(row: dict[str, Any]) -> dict[str, Any]:
    cpu = _number(row.get("process_cpu_usage", row.get("process_cpu")))
    response_time = _number(row.get("avg_response_time_ms"))
    executor = _number(row.get("executor_active"))
    heap = _number(row.get("heap_pct"))
    gc = _number(row.get("gc_overhead"))
    active = _number(row.get("active_requests"))

    score = min(
        cpu * 0.35
        + heap * 0.35
        + gc * 100 * 0.15
        + min(active, 100) * 0.10
        + min(response_time / 10, 100) * 0.10
        + min(executor, 100) * 0.05,
        100.0,
    )
    if score >= 75:
        status = "CRITICAL"
    elif score >= 45:
        status = "WARNING"
    else:
        status = "HEALTHY"

    return {
        "service_name": row.get("service_name", "UNKNOWN"),
        "health_score": round(score, 2),
        "status": status,
        "cpu_pct": cpu,
        "heap_pct": heap,
        "gc_overhead": gc,
        "active_requests": active,
        "avg_response_time_ms": response_time,
        "executor_active": executor,
        "reason": (
            f"CPU={cpu:.2f}%, heap={heap:.2f}%, "
            f"GC={gc:.4f}, active requests={active:.2f}, "
            f"response time={response_time:.2f}ms"
        ),
    }


def collect_service_health(client: Any, run_id: str) -> list[dict[str, Any]]:
    query = (
        'SELECT * FROM "aiperf_service_metrics" '
        f"WHERE run_id='{run_id}'"
    )
    rows = list(client.query(query).get_points())
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("service_name", "UNKNOWN"))
        latest[name] = row
    return [assess_service(row) for row in latest.values()]


def persist_service_health(
    client: Any,
    run_id: str,
    assessments: list[dict[str, Any]],
) -> None:
    points = []
    for assessment in assessments:
        points.append(
            {
                "measurement": "aiperf_service_health",
                "tags": {
                    "run_id": str(run_id),
                    "service_name": str(assessment["service_name"]),
                    "status": str(assessment["status"]),
                },
                "fields": {
                    "health_score": float(assessment["health_score"]),
                    "cpu_pct": float(assessment["cpu_pct"]),
                    "heap_pct": float(assessment["heap_pct"]),
                    "gc_overhead": float(assessment["gc_overhead"]),
                    "active_requests": float(assessment["active_requests"]),
                    "avg_response_time_ms": float(
                        assessment["avg_response_time_ms"]
                    ),
                    "executor_active": float(assessment["executor_active"]),
                    "reason": str(assessment["reason"]),
                },
            }
        )
    if points:
        client.write_points(points)


def main() -> int:
    run_id = os.getenv("RUN_ID")
    if not run_id:
        raise RuntimeError("RUN_ID environment variable is required")
    client = InfluxDBClient(
        host=os.getenv("INFLUX_HOST", "localhost"),
        port=int(os.getenv("INFLUX_PORT", "8086")),
        database=os.getenv("INFLUX_DATABASE", "jmeter"),
    )
    assessments = collect_service_health(client, run_id)
    if not assessments:
        raise RuntimeError(f"No service metrics found for RUN_ID {run_id}")
    persist_service_health(client, run_id, assessments)
    for assessment in assessments:
        print(
            f"{assessment['service_name']}: "
            f"{assessment['status']} ({assessment['health_score']:.2f})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

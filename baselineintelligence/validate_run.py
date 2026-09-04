"""Validate that the current RUN_ID has complete upstream intelligence data."""

from __future__ import annotations

import os
from typing import Any

from influxdb import InfluxDBClient


REQUIRED_MEASUREMENTS = {
    "aiperf_transaction_history": ("run_id",),
    "aiperf_execution_history": ("run_id",),
    "aiperf_service_metrics": ("run_id",),
    "aiperf_transaction_comparison": ("current_run_id",),
    "aiperf_service_comparison": ("current_run_id",),
    "aiperf_variance_ranking": ("run_id",),
}


def _query_for_run(client: Any, measurement: str, run_id: str):
    clauses = [
        f"{field}='{run_id}'"
        for field in REQUIRED_MEASUREMENTS[measurement]
    ]
    query = f'SELECT * FROM "{measurement}" WHERE ' + " OR ".join(clauses)
    return list(client.query(query).get_points())


def validate_run(client: Any, run_id: str) -> dict[str, int]:
    if not run_id.strip():
        raise ValueError("RUN_ID must not be empty")

    counts = {}
    missing = []
    for measurement in REQUIRED_MEASUREMENTS:
        count = len(_query_for_run(client, measurement, run_id))
        counts[measurement] = count
        if count == 0:
            missing.append(measurement)

    if missing:
        raise RuntimeError(
            f"RUN_ID {run_id} is missing required measurements: {', '.join(missing)}"
        )
    return counts


def main() -> int:
    run_id = os.getenv("RUN_ID")
    if not run_id:
        raise RuntimeError("RUN_ID environment variable is required")

    client = InfluxDBClient(
        host=os.getenv("INFLUX_HOST", "localhost"),
        port=int(os.getenv("INFLUX_PORT", "8086")),
        database=os.getenv("INFLUX_DATABASE", "jmeter"),
    )
    counts = validate_run(client, run_id)
    for measurement, count in counts.items():
        print(f"{measurement}: {count} point(s) for {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

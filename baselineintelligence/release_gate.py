"""Fail CI when the findings package says the release must be blocked."""

from __future__ import annotations

import json
import os
from typing import Any

from influxdb import InfluxDBClient


def get_latest_package(client: Any, run_id: str) -> dict[str, Any]:
    query = (
        'SELECT * FROM "aiperf_findings_package" '
        f"WHERE run_id='{run_id}' ORDER BY time DESC LIMIT 1"
    )
    rows = list(client.query(query).get_points())
    if not rows:
        raise RuntimeError(f"No findings package found for RUN_ID {run_id}")

    findings_json = rows[0].get("findings_json")
    if not findings_json:
        raise RuntimeError(f"Findings package for RUN_ID {run_id} has no findings_json")
    try:
        package = json.loads(findings_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Findings package contains invalid JSON") from exc
    if not isinstance(package, dict):
        raise RuntimeError("Findings package JSON must be an object")
    return package


def evaluate_release(package: dict[str, Any]) -> tuple[bool, str]:
    release_impact = package.get("release_impact") or {}
    decision = str(release_impact.get("decision", "")).upper()
    if decision not in {"PROCEED", "CONDITIONAL", "BLOCK"}:
        raise RuntimeError("Findings package has no valid release decision")
    if decision == "BLOCK":
        return False, str(release_impact.get("rationale", "Release blocked"))
    return True, f"Release decision: {decision}"


def main() -> int:
    run_id = os.getenv("RUN_ID")
    if not run_id:
        raise RuntimeError("RUN_ID environment variable is required")

    client = InfluxDBClient(
        host=os.getenv("INFLUX_HOST", "localhost"),
        port=int(os.getenv("INFLUX_PORT", "8086")),
        database=os.getenv("INFLUX_DATABASE", "jmeter"),
    )
    package = get_latest_package(client, run_id)
    allowed, message = evaluate_release(package)
    print(message)
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())

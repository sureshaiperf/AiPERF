"""Fail CI when the findings package says the release must be blocked."""

from __future__ import annotations

import json
import os
import ast
from typing import Any

from influxdb import InfluxDBClient


def parse_findings_package(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise RuntimeError("Findings package contains invalid JSON")

    candidates = [value]
    if "\\n" in value:
        candidates.append(value.encode("utf-8").decode("unicode_escape"))

    for candidate in candidates:
        try:
            package = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                package = ast.literal_eval(candidate)
            except (SyntaxError, ValueError):
                continue
        if isinstance(package, dict):
            return package

    raise RuntimeError("Findings package contains invalid JSON")


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
    return parse_findings_package(findings_json)


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

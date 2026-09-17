"""Enforce the AiPERF release decision for one Jenkins execution."""

from __future__ import annotations

import ast
import json
import os
import sys
from typing import Any

from influxdb import InfluxDBClient


VALID_DECISIONS = {"PROCEED", "CONDITIONAL", "OBSERVE", "BLOCK"}
NON_BLOCKING_DECISIONS = {"PROCEED", "CONDITIONAL", "OBSERVE"}


def escape_influxql_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def parse_findings_package(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("Findings package contains invalid or empty JSON")

    candidates = [value]
    if "\\n" in value or "\\r" in value or "\\t" in value:
        try:
            candidates.append(value.encode("utf-8").decode("unicode_escape"))
        except UnicodeDecodeError:
            pass

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
    safe_run_id = escape_influxql_string(run_id)
    query = (
        'SELECT "findings_json" FROM "aiperf_findings_package" '
        f"WHERE \"run_id\"='{safe_run_id}' ORDER BY time DESC LIMIT 1"
    )
    rows = list(client.query(query).get_points())
    if not rows:
        raise RuntimeError(f"No findings package found for RUN_ID {run_id}")

    findings_json = rows[0].get("findings_json")
    if not findings_json:
        raise RuntimeError(
            f"Findings package for RUN_ID {run_id} has no findings_json"
        )
    return parse_findings_package(findings_json)


def evaluate_release(package: dict[str, Any]) -> tuple[bool, str]:
    release_impact = package.get("release_impact") or {}
    if not isinstance(release_impact, dict):
        raise RuntimeError("Findings package release_impact must be an object")

    decision = str(release_impact.get("decision", "")).strip().upper()
    baseline_status = str(
        release_impact.get("baseline_status", "UNKNOWN")
    ).strip().upper()
    rationale = str(
        release_impact.get("rationale", "No rationale supplied")
    ).strip()

    if decision not in VALID_DECISIONS:
        raise RuntimeError(
            "Findings package has no valid release decision. "
            f"Received: {decision or 'EMPTY'}"
        )

    if decision == "BLOCK":
        return (
            False,
            "Release decision: BLOCK | "
            f"Baseline status: {baseline_status} | {rationale}",
        )

    if decision not in NON_BLOCKING_DECISIONS:
        raise RuntimeError(f"Unhandled release decision: {decision}")

    gate_result = "NON_BLOCKING"
    return (
        True,
        f"Release decision: {decision} | "
        f"Baseline status: {baseline_status} | "
        f"Gate result: {gate_result} | {rationale}",
    )


def main() -> int:
    run_id = os.getenv("RUN_ID")
    if run_id is None or not run_id.strip():
        raise RuntimeError("RUN_ID environment variable is required")
    run_id = run_id.strip()

    client = InfluxDBClient(
        host=os.getenv("INFLUX_HOST", "localhost"),
        port=int(os.getenv("INFLUX_PORT", "8086")),
        username=os.getenv("INFLUX_USER"),
        password=os.getenv("INFLUX_PASSWORD"),
        database=os.getenv("INFLUX_DATABASE", "jmeter"),
        timeout=int(os.getenv("INFLUX_TIMEOUT_SECONDS", "30")),
    )

    try:
        client.ping()
        package = get_latest_package(client, run_id)
        allowed, message = evaluate_release(package)
        print(message)
        return 0 if allowed else 1
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Release Gate Failed")
        print(f"Error Type: {type(exc).__name__}")
        print(f"Error: {exc}")
        raise SystemExit(1)

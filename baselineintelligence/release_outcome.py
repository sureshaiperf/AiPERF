"""Persist observed release outcomes separately from predicted decisions."""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from typing import Any

from influxdb import InfluxDBClient


MEASUREMENT = "aiperf_release_outcome"
VALID_OUTCOMES = {"RELEASED", "ROLLED_BACK", "INCIDENT", "SUCCESS", "FAILED", "UNKNOWN"}


def write_release_outcome(
    client: Any,
    run_id: str,
    outcome: str,
    *,
    release_id: str | None = None,
    notes: str = "",
    observed_at: str | None = None,
) -> None:
    normalized = outcome.strip().upper()
    if normalized not in VALID_OUTCOMES:
        raise ValueError(
            f"Unsupported release outcome {outcome!r}; "
            f"expected one of {sorted(VALID_OUTCOMES)}"
        )
    client.write_points([{
        "measurement": MEASUREMENT,
        "time": observed_at or datetime.now(timezone.utc).isoformat(),
        "tags": {
            "run_id": str(run_id),
            "outcome": normalized,
        },
        "fields": {
            "release_id": release_id or "",
            "notes": notes,
            "outcome": normalized,
        },
    }])


def main() -> int:
    parser = argparse.ArgumentParser(description="Record an observed AiPERF release outcome.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--outcome", required=True, choices=sorted(VALID_OUTCOMES))
    parser.add_argument("--release-id")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    client = InfluxDBClient(
        host=os.getenv("INFLUXDB_HOST", "localhost"),
        port=int(os.getenv("INFLUXDB_PORT", "8086")),
        database=os.getenv("INFLUXDB_DATABASE", "jmeter"),
    )
    try:
        write_release_outcome(
            client,
            args.run_id,
            args.outcome,
            release_id=args.release_id,
            notes=args.notes,
        )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

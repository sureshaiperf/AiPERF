"""Safely reset versioned AiPERF-derived InfluxDB measurements.

The raw ``jmeter`` measurement is intentionally excluded. Run without
``--confirm`` to inspect the cleanup plan and export targets only.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from influxdb import InfluxDBClient


CLEANUP_VERSION = "1"
RAW_MEASUREMENTS = {"jmeter"}
DERIVED_MEASUREMENTS = (
    "aiperf_baseline",
    "aiperf_transaction_history",
    "aiperf_execution_history",
    "aiperf_execution_fingerprint",
    "aiperf_analysis",
    "aiperf_run_comparison",
    "aiperf_transaction_comparison",
    "aiperf_service_metrics",
    "aiperf_service_comparison",
    "aiperf_service_health",
    "aiperf_variance_ranking",
    "aiperf_trend_analysis",
    "aiperf_forecast",
    "aiperf_alerts",
    "aiperf_anomaly_detection",
    "aiperf_bottleneck_intelligence",
    "aiperf_correlation_intelligence",
    "aiperf_similar_execution",
    "aiperf_historical_similarity",
    "aiperf_release_readiness",
    "aiperf_ai_insights",
    "aiperf_findings_package",
    "aiperf_release_outcome",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up and optionally delete AiPERF-derived InfluxDB measurements."
    )
    parser.add_argument("--host", default=os.getenv("INFLUX_HOST", "localhost"))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("INFLUX_PORT", "8086"))
    )
    parser.add_argument(
        "--database", default=os.getenv("INFLUX_DATABASE", "jmeter")
    )
    parser.add_argument(
        "--backup-dir",
        default=os.getenv(
            "AIPERF_CLEANUP_BACKUP_DIR",
            str(Path.cwd() / "influx-backups"),
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Export and drop existing targeted measurements.",
    )
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="MEASUREMENT",
        help="Keep a derived measurement; may be specified more than once.",
    )
    return parser.parse_args()


def list_measurements(client: Any) -> set[str]:
    result = client.query("SHOW MEASUREMENTS")
    return {
        str(point["name"])
        for point in result.get_points()
        if point.get("name")
    }


def export_measurement(client: Any, measurement: str, path: Path) -> int:
    result = client.query(f'SELECT * FROM "{measurement}"')
    rows = list(result.get_points())
    path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=True, default=str),
        encoding="utf-8",
    )
    return len(rows)


def main() -> int:
    args = parse_args()
    keep = set(args.keep)
    unknown_raw = keep & RAW_MEASUREMENTS
    if unknown_raw:
        raise ValueError(
            "Refusing to clean raw measurement(s): "
            + ", ".join(sorted(unknown_raw))
        )

    client = InfluxDBClient(
        host=args.host,
        port=args.port,
        database=args.database,
    )
    client.ping()
    existing = list_measurements(client)
    targets = [
        measurement
        for measurement in DERIVED_MEASUREMENTS
        if measurement in existing and measurement not in keep
    ]

    print(f"AiPERF cleanup version : {CLEANUP_VERSION}")
    print(f"Database               : {args.database}")
    print("Raw measurement        : jmeter (preserved)")
    print(f"Derived targets        : {len(targets)}")
    if keep:
        print(f"Kept measurements      : {', '.join(sorted(keep))}")
    if not targets:
        print("No targeted measurements exist. Nothing to clean.")
        return 0

    if not args.confirm:
        print("\nDRY RUN: no data was changed.")
        print("Would back up and drop:")
        for measurement in targets:
            print(f"  - {measurement}")
        print("\nRe-run with --confirm to perform the backup and cleanup.")
        return 0

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path(args.backup_dir) / f"cleanup-v{CLEANUP_VERSION}-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "cleanup_version": CLEANUP_VERSION,
        "database": args.database,
        "host": args.host,
        "created_at": timestamp,
        "raw_measurements_preserved": sorted(RAW_MEASUREMENTS),
        "measurements": {},
    }

    for measurement in targets:
        backup_path = backup_dir / f"{measurement}.json"
        count = export_measurement(client, measurement, backup_path)
        manifest["measurements"][measurement] = {
            "backup_file": backup_path.name,
            "row_count": count,
        }

    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    for measurement in targets:
        client.query(f'DROP MEASUREMENT "{measurement}"')
        print(f"Dropped {measurement}")

    print(f"Backup written to: {backup_dir}")
    print("Raw jmeter measurement was preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

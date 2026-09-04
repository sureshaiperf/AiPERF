"""Detect anomalous performance metrics in AiPERF execution history.

The detector compares the latest execution with prior executions using a
robust median/MAD score.  It can be used as a library, from the command line,
or as an InfluxDB writer.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any


DEFAULT_MEASUREMENT = "aiperf_execution_history"
OUTPUT_MEASUREMENT = "aiperf_anomaly_detection"
DEFAULT_METRICS = ("avg_rt", "p95", "p99", "throughput", "error_rate")


def _as_number(value: Any) -> float | None:
    """Return finite numeric values only."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _median_absolute_deviation(values: list[float], median: float) -> float:
    return statistics.median(abs(value - median) for value in values)


def _score(value: float, history: list[float]) -> tuple[float, float, str]:
    """Return anomaly score, baseline, and scoring method."""
    baseline = statistics.median(history)
    mad = _median_absolute_deviation(history, baseline)

    if mad > 0:
        # 0.6745 makes MAD scores comparable to standard deviations.
        return abs(0.6745 * (value - baseline) / mad), baseline, "mad"

    deviation = statistics.pstdev(history)
    if deviation > 0:
        return abs((value - baseline) / deviation), baseline, "stddev"

    return (math.inf if value != baseline else 0.0), baseline, "constant"


def detect_anomalies(
    points: Iterable[Mapping[str, Any]],
    *,
    metrics: Iterable[str] = DEFAULT_METRICS,
    threshold: float = 3.5,
    minimum_history: int = 5,
) -> list[dict[str, Any]]:
    """Analyze the newest point and return one result per available metric.

    ``points`` must contain at least one latest point and may be in any order.
    The latest point is selected by its Influx ``time`` value when present,
    otherwise the input order is used.  A metric is skipped when it has fewer
    than ``minimum_history`` usable historical values.
    """
    if threshold <= 0:
        raise ValueError("threshold must be greater than zero")
    if minimum_history < 1:
        raise ValueError("minimum_history must be at least one")

    rows = [dict(point) for point in points]
    if not rows:
        return []

    rows.sort(key=lambda row: str(row.get("time", "")))
    latest = rows[-1]
    history_rows = rows[:-1]
    latest_run_id = latest.get("run_id") or latest.get("build_id") or "UNKNOWN"
    results: list[dict[str, Any]] = []

    for metric in metrics:
        current = _as_number(latest.get(metric))
        history = [
            number
            for row in history_rows
            if (number := _as_number(row.get(metric))) is not None
        ]
        if current is None or len(history) < minimum_history:
            continue

        score, baseline, method = _score(current, history)
        deviation_pct = (
            ((current - baseline) / abs(baseline)) * 100
            if baseline != 0
            else (0.0 if current == 0 else math.inf)
        )
        is_anomaly = score >= threshold
        direction = "stable"
        if current > baseline:
            direction = "increase"
        elif current < baseline:
            direction = "decrease"

        if is_anomaly:
            severity = "CRITICAL" if score >= threshold * 2 else "WARNING"
        else:
            severity = "NORMAL"

        results.append(
            {
                "run_id": latest_run_id,
                "metric": metric,
                "value": current,
                "baseline": baseline,
                "deviation_pct": deviation_pct,
                "anomaly_score": score,
                "is_anomaly": is_anomaly,
                "severity": severity,
                "direction": direction,
                "scoring_method": method,
                "sample_count": len(history),
            }
        )

    return results


def to_influx_points(
    results: Iterable[Mapping[str, Any]],
    *,
    measurement: str = OUTPUT_MEASUREMENT,
    timestamp: str | None = None,
) -> list[dict[str, Any]]:
    """Convert detector results to InfluxDB line-protocol JSON points."""
    point_time = timestamp or datetime.now(timezone.utc).isoformat()
    output = []
    for result in results:
        deviation_pct = float(result["deviation_pct"])
        anomaly_score = float(result["anomaly_score"])
        output.append(
            {
                "measurement": measurement,
                "time": point_time,
                "tags": {
                    "run_id": str(result["run_id"]),
                    "metric": str(result["metric"]),
                    "severity": str(result["severity"]),
                    "direction": str(result["direction"]),
                },
                "fields": {
                    "value": float(result["value"]),
                    "baseline": float(result["baseline"]),
                    "deviation_pct": deviation_pct
                    if math.isfinite(deviation_pct)
                    else 999999.0,
                    "anomaly_score": anomaly_score
                    if math.isfinite(anomaly_score)
                    else 999999.0,
                    "is_anomaly": int(bool(result["is_anomaly"])),
                    "sample_count": int(result["sample_count"]),
                },
            }
        )
    return output


def run_anomaly_detection(
    client: Any,
    *,
    measurement: str = DEFAULT_MEASUREMENT,
    metrics: Iterable[str] = DEFAULT_METRICS,
    threshold: float = 3.5,
    minimum_history: int = 5,
    write: bool = True,
) -> list[dict[str, Any]]:
    """Read execution history, detect anomalies, and optionally persist them."""
    query = f'SELECT * FROM "{measurement}" ORDER BY time ASC'
    rows = list(client.query(query).get_points())
    results = detect_anomalies(
        rows,
        metrics=metrics,
        threshold=threshold,
        minimum_history=minimum_history,
    )
    if write and results:
        client.write_points(to_influx_points(results))
    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=3.5)
    parser.add_argument("--minimum-history", type=int, default=5)
    parser.add_argument("--metrics", nargs="+", default=list(DEFAULT_METRICS))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    from influxdb import InfluxDBClient

    client = InfluxDBClient(
        host=os.getenv("INFLUX_HOST", "localhost"),
        port=int(os.getenv("INFLUX_PORT", "8086")),
        database=os.getenv("INFLUX_DATABASE", "jmeter"),
    )
    results = run_anomaly_detection(
        client,
        metrics=args.metrics,
        threshold=args.threshold,
        minimum_history=args.minimum_history,
        write=not args.dry_run,
    )
    if args.as_json:
        print(json.dumps(results, allow_nan=False))
    else:
        anomalies = [result for result in results if result["is_anomaly"]]
        print(f"Analyzed {len(results)} metric(s); found {len(anomalies)} anomal(y/ies).")
        for result in results:
            print(
                f"{result['metric']}: {result['severity']} "
                f"value={result['value']:.2f} baseline={result['baseline']:.2f} "
                f"score={result['anomaly_score']:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

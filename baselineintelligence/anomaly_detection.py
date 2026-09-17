"""Detect direction-aware anomalies in AiPERF execution history.

The detector uses a robust median/MAD score and enforces metric semantic
compatibility. In particular, throughput-rate records are never compared with
legacy records where the ``throughput`` field stored total sample count.

Compatible with InfluxDB OSS 1.8.x and the Python ``influxdb`` client.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any


DEFAULT_MEASUREMENT = "aiperf_execution_history"
OUTPUT_MEASUREMENT = "aiperf_anomaly_detection"
DEFAULT_METRICS = ("avg_rt", "p95", "p99", "throughput", "error_rate")
THROUGHPUT_METRICS = {"throughput", "throughput_rps"}

STATUS_ANALYZED = "ANALYZED"
STATUS_METRIC_UNAVAILABLE = "METRIC_UNAVAILABLE"
STATUS_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
STATUS_INSUFFICIENT_COMPATIBLE_HISTORY = "INSUFFICIENT_COMPATIBLE_HISTORY"


def _as_number(value: Any) -> float | None:
    """Return finite numeric values only."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any, default: int = 0) -> int:
    number = _as_number(value)
    return int(number) if number is not None else default


def _safe_run_id(value: Any) -> str:
    run_id = str(value or "").strip()
    if not run_id:
        raise ValueError("RUN_ID is required")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", run_id):
        raise ValueError("RUN_ID contains unsupported characters")
    return run_id


def _median_absolute_deviation(values: list[float], median: float) -> float:
    return statistics.median(abs(value - median) for value in values)


def _score(value: float, history: list[float]) -> tuple[float, float, str]:
    """Return anomaly score, historical median, and scoring method."""
    baseline = statistics.median(history)
    mad = _median_absolute_deviation(history, baseline)
    if mad > 0:
        return abs(0.6745 * (value - baseline) / mad), baseline, "mad"

    deviation = statistics.pstdev(history)
    if deviation > 0:
        return abs((value - baseline) / deviation), baseline, "stddev"

    return (math.inf if value != baseline else 0.0), baseline, "constant"


def _clean_history(values: list[float]) -> list[float]:
    """Exclude implausible positive spikes without changing metric units."""
    if len(values) < 4:
        return values
    median = statistics.median(values)
    mad = _median_absolute_deviation(values, median)
    limit = max(median * 5, median + (mad * 6)) if median > 0 else math.inf
    cleaned = [value for value in values if value <= limit]
    return cleaned if len(cleaned) >= 3 else values


def _semantic_version(row: Mapping[str, Any]) -> int:
    """Return execution-history throughput semantic version.

    Version 1 means the legacy ``throughput`` field contains total samples.
    Version 2 means throughput is a rate in requests per second.
    """
    explicit = _integer(row.get("throughput_semantic_version"), 0)
    if explicit >= 2:
        return 2
    if _as_number(row.get("throughput_rps")) is not None:
        return 2
    return 1


def _metric_value(row: Mapping[str, Any], metric: str) -> float | None:
    """Resolve one metric using its authoritative semantic contract."""
    if metric in THROUGHPUT_METRICS:
        if _semantic_version(row) < 2:
            return None
        value = _as_number(row.get("throughput_rps"))
        if value is not None:
            return value
        return _as_number(row.get("throughput"))
    return _as_number(row.get(metric))


def _metric_direction(metric: str, current: float, baseline: float) -> str:
    if current == baseline:
        return "stable"
    if metric in THROUGHPUT_METRICS:
        return "degradation" if current < baseline else "improvement"
    return "degradation" if current > baseline else "improvement"


def _result_status(
    *,
    metric: str,
    current: float | None,
    history_count: int,
    minimum_history: int,
) -> str:
    if current is None:
        if metric in THROUGHPUT_METRICS:
            return STATUS_INSUFFICIENT_COMPATIBLE_HISTORY
        return STATUS_METRIC_UNAVAILABLE
    if history_count < minimum_history:
        if metric in THROUGHPUT_METRICS:
            return STATUS_INSUFFICIENT_COMPATIBLE_HISTORY
        return STATUS_INSUFFICIENT_HISTORY
    return STATUS_ANALYZED


def detect_anomalies(
    points: Iterable[Mapping[str, Any]],
    *,
    metrics: Iterable[str] = DEFAULT_METRICS,
    threshold: float = 3.5,
    minimum_history: int = 5,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return one deterministic result for every requested metric."""
    if threshold <= 0:
        raise ValueError("threshold must be greater than zero")
    if minimum_history < 1:
        raise ValueError("minimum_history must be at least one")

    rows = [dict(point) for point in points]
    if not rows:
        return []
    rows.sort(key=lambda row: str(row.get("time", "")))

    if run_id is None:
        latest = rows[-1]
        history_rows = rows[:-1]
    else:
        selected_run = _safe_run_id(run_id)
        matching = [
            row
            for row in rows
            if str(row.get("run_id") or row.get("build_id")) == selected_run
        ]
        if not matching:
            return []
        latest = matching[-1]
        history_rows = [
            row
            for row in rows
            if str(row.get("run_id") or row.get("build_id")) != selected_run
        ]

    latest_run_id = str(
        latest.get("run_id") or latest.get("build_id") or "UNKNOWN"
    )
    latest_semantic_version = _semantic_version(latest)
    results: list[dict[str, Any]] = []

    for metric in metrics:
        normalized_metric = (
            "throughput" if metric in THROUGHPUT_METRICS else str(metric)
        )
        current = _metric_value(latest, metric)

        compatible_history: list[float] = []
        incompatible_count = 0
        for row in history_rows:
            if metric in THROUGHPUT_METRICS and _semantic_version(row) < 2:
                incompatible_count += 1
                continue
            value = _metric_value(row, metric)
            if value is not None:
                compatible_history.append(value)

        status = _result_status(
            metric=metric,
            current=current,
            history_count=len(compatible_history),
            minimum_history=minimum_history,
        )

        if status != STATUS_ANALYZED:
            results.append(
                {
                    "run_id": latest_run_id,
                    "metric": normalized_metric,
                    "status": status,
                    "value": current if current is not None else 0.0,
                    "baseline": 0.0,
                    "deviation_pct": 0.0,
                    "anomaly_score": 0.0,
                    "is_anomaly": False,
                    "severity": "NOT_EVALUATED",
                    "direction": "not_evaluated",
                    "scoring_method": "not_evaluated",
                    "sample_count": len(compatible_history),
                    "minimum_history": minimum_history,
                    "incompatible_sample_count": incompatible_count,
                    "semantic_version": (
                        latest_semantic_version
                        if metric in THROUGHPUT_METRICS
                        else 1
                    ),
                    "semantic_compatibility": (
                        "INSUFFICIENT_COMPATIBLE_HISTORY"
                        if metric in THROUGHPUT_METRICS
                        else "NOT_APPLICABLE"
                    ),
                }
            )
            continue

        history = _clean_history(compatible_history)
        if len(history) < minimum_history:
            results.append(
                {
                    "run_id": latest_run_id,
                    "metric": normalized_metric,
                    "status": (
                        STATUS_INSUFFICIENT_COMPATIBLE_HISTORY
                        if metric in THROUGHPUT_METRICS
                        else STATUS_INSUFFICIENT_HISTORY
                    ),
                    "value": float(current),
                    "baseline": 0.0,
                    "deviation_pct": 0.0,
                    "anomaly_score": 0.0,
                    "is_anomaly": False,
                    "severity": "NOT_EVALUATED",
                    "direction": "not_evaluated",
                    "scoring_method": "not_evaluated",
                    "sample_count": len(history),
                    "minimum_history": minimum_history,
                    "incompatible_sample_count": incompatible_count,
                    "semantic_version": (
                        latest_semantic_version
                        if metric in THROUGHPUT_METRICS
                        else 1
                    ),
                    "semantic_compatibility": (
                        "INSUFFICIENT_COMPATIBLE_HISTORY"
                        if metric in THROUGHPUT_METRICS
                        else "NOT_APPLICABLE"
                    ),
                }
            )
            continue

        score, baseline, method = _score(float(current), history)
        deviation_pct = (
            ((float(current) - baseline) / abs(baseline)) * 100.0
            if baseline != 0
            else (0.0 if float(current) == 0 else math.inf)
        )
        is_anomaly = score >= threshold
        direction = _metric_direction(metric, float(current), baseline)
        severity = (
            "CRITICAL"
            if is_anomaly and score >= threshold * 2
            else "WARNING"
            if is_anomaly
            else "NORMAL"
        )

        results.append(
            {
                "run_id": latest_run_id,
                "metric": normalized_metric,
                "status": STATUS_ANALYZED,
                "value": float(current),
                "baseline": baseline,
                "deviation_pct": deviation_pct,
                "anomaly_score": score,
                "is_anomaly": is_anomaly,
                "severity": severity,
                "direction": direction,
                "scoring_method": method,
                "sample_count": len(history),
                "minimum_history": minimum_history,
                "incompatible_sample_count": incompatible_count,
                "semantic_version": (
                    latest_semantic_version
                    if metric in THROUGHPUT_METRICS
                    else 1
                ),
                "semantic_compatibility": (
                    "FULL"
                    if metric in THROUGHPUT_METRICS
                    else "NOT_APPLICABLE"
                ),
            }
        )

    return results


def to_influx_points(
    results: Iterable[Mapping[str, Any]],
    *,
    measurement: str = OUTPUT_MEASUREMENT,
    timestamp: str | None = None,
) -> list[dict[str, Any]]:
    """Convert results to InfluxDB OSS 1.8-compatible JSON points."""
    point_time = timestamp or datetime.now(timezone.utc).isoformat()
    output: list[dict[str, Any]] = []

    for result in results:
        deviation_pct = float(result.get("deviation_pct", 0.0))
        anomaly_score = float(result.get("anomaly_score", 0.0))
        output.append(
            {
                "measurement": measurement,
                "time": point_time,
                "tags": {
                    "run_id": str(result["run_id"]),
                    "metric": str(result["metric"]),
                    "severity": str(result["severity"]),
                    "direction": str(result["direction"]),
                    "status": str(result["status"]),
                    "semantic_compatibility": str(
                        result["semantic_compatibility"]
                    ),
                },
                "fields": {
                    "value": float(result.get("value", 0.0)),
                    "baseline": float(result.get("baseline", 0.0)),
                    "deviation_pct": (
                        deviation_pct if math.isfinite(deviation_pct) else 999999.0
                    ),
                    "anomaly_score": (
                        anomaly_score if math.isfinite(anomaly_score) else 999999.0
                    ),
                    "is_anomaly": int(bool(result.get("is_anomaly", False))),
                    "sample_count": int(result.get("sample_count", 0)),
                    "minimum_history": int(result.get("minimum_history", 0)),
                    "incompatible_sample_count": int(
                        result.get("incompatible_sample_count", 0)
                    ),
                    "semantic_version": int(result.get("semantic_version", 1)),
                    "scoring_method": str(result.get("scoring_method", "unknown")),
                },
            }
        )

    return output


def _escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def run_anomaly_detection(
    client: Any,
    *,
    measurement: str = DEFAULT_MEASUREMENT,
    metrics: Iterable[str] = DEFAULT_METRICS,
    threshold: float = 3.5,
    minimum_history: int = 5,
    write: bool = True,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read history, detect anomalies, and replace this run's output."""
    selected_run = _safe_run_id(run_id) if run_id else None
    query = f'SELECT * FROM "{measurement}" ORDER BY time ASC'
    rows = list(client.query(query).get_points())
    results = detect_anomalies(
        rows,
        metrics=metrics,
        threshold=threshold,
        minimum_history=minimum_history,
        run_id=selected_run,
    )

    if write and selected_run:
        client.query(
            f'DROP SERIES FROM "{OUTPUT_MEASUREMENT}" '
            f'WHERE "run_id"=\'{_escape_literal(selected_run)}\''
        )
        points = to_influx_points(results)
        if points and client.write_points(points) is False:
            raise RuntimeError("InfluxDB rejected anomaly-detection records")

    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=3.5)
    parser.add_argument("--minimum-history", type=int, default=5)
    parser.add_argument("--metrics", nargs="+", default=list(DEFAULT_METRICS))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-id", default=os.getenv("RUN_ID"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    selected_run = _safe_run_id(args.run_id)

    from influxdb import InfluxDBClient

    client = InfluxDBClient(
        host=os.getenv("INFLUX_HOST", "localhost"),
        port=int(os.getenv("INFLUX_PORT", "8086")),
        username=os.getenv("INFLUX_USER") or None,
        password=os.getenv("INFLUX_PASSWORD") or None,
        database=os.getenv("INFLUX_DATABASE", os.getenv("INFLUX_DB", "jmeter")),
        timeout=int(os.getenv("INFLUX_TIMEOUT_SECONDS", "30")),
    )

    try:
        client.ping()
        results = run_anomaly_detection(
            client,
            metrics=args.metrics,
            threshold=args.threshold,
            minimum_history=args.minimum_history,
            write=not args.dry_run,
            run_id=selected_run,
        )

        if args.as_json:
            print(json.dumps(results, allow_nan=False))
            return 0

        analyzed = [
            result for result in results if result["status"] == STATUS_ANALYZED
        ]
        anomalies = [result for result in analyzed if result["is_anomaly"]]
        not_evaluated = [
            result for result in results if result["status"] != STATUS_ANALYZED
        ]

        print(
            f"Analyzed {len(analyzed)} metric(s); found {len(anomalies)} "
            f"anomal(y/ies); skipped {len(not_evaluated)} metric(s)."
        )
        for result in results:
            if result["status"] != STATUS_ANALYZED:
                print(
                    f"{result['metric']}: {result['status']} "
                    f"compatible_history={result['sample_count']} "
                    f"incompatible_history={result['incompatible_sample_count']}"
                )
                continue
            print(
                f"{result['metric']}: {result['severity']} "
                f"value={result['value']:.2f} "
                f"baseline={result['baseline']:.2f} "
                f"score={result['anomaly_score']:.2f} "
                f"direction={result['direction']}"
            )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Anomaly Detection Failed")
        print(f"Error Type: {type(exc).__name__}")
        print(f"Error: {exc}")
        raise SystemExit(1)

"""Read exact run-scoped aggregate metrics from a JMeter CSV JTL file."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def read_jtl(path: str | Path) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[tuple[float, bool]]] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            label = row.get("label")
            if not label or label in {"internal"}:
                continue
            try:
                elapsed = float(row["elapsed"])
            except (KeyError, TypeError, ValueError):
                continue
            success = str(row.get("success", "true")).lower() == "true"
            grouped.setdefault(label, []).append((elapsed, success))

    grouped["all"] = [
        sample
        for label, samples in grouped.items()
        if label != "all"
        for sample in samples
    ]
    result: dict[str, dict[str, float]] = {}
    for label, samples in grouped.items():
        if not samples:
            continue
        durations = [sample[0] for sample in samples]
        errors = sum(1 for _, success in samples if not success)
        result[label] = {
            "samples": float(len(samples)),
            "avg_rt": sum(durations) / len(durations),
            "p50": _percentile(durations, 0.50),
            "p90": _percentile(durations, 0.90),
            "p95": _percentile(durations, 0.95),
            "p99": _percentile(durations, 0.99),
            "errors": float(errors),
            "error_pct": (errors / len(samples)) * 100,
        }
    return result

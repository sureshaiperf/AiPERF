"""Architect-level bottleneck intelligence with explicit InfluxDB v1 persistence."""
from __future__ import annotations
import math
from collections.abc import Iterable, Mapping
from typing import Any

def _n(value: Any) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else 0.0
    except (TypeError, ValueError):
        return 0.0

def analyze_bottlenecks(rows: Iterable[Mapping[str, Any]], *, run_id: str, limit: int = 10) -> dict[str, Any]:
    ranked = []
    for row in rows:
        if str(row.get("run_id", row.get("current_run_id", ""))) != str(run_id):
            continue
        latency = abs(_n(row.get("variance_pct", row.get("p95", row.get("avg_rt_variance_pct")))))
        errors = abs(_n(row.get("error_rate", row.get("error_pct"))))
        resource = max(abs(_n(row.get("cpu_pct", row.get("process_cpu")))),
                        abs(_n(row.get("heap_pct", row.get("heap_pct_variance_pct")))))
        score = latency + errors * 2 + resource * .5
        if score <= 0: continue
        entity = str(row.get("entity_name", row.get("service_name", row.get("transaction", "UNKNOWN"))))
        impacted_service = row.get("service_name") or (entity if "service" in str(row).lower() else "")
        impacted_transaction = row.get("transaction") or (entity if not impacted_service else "")
        ranked.append({"entity": entity, "score": round(score, 4), "reason": f"Latency impact {latency:.2f}%, errors {errors:.2f}%, resource pressure {resource:.2f}%", "impacted_services": [impacted_service] if impacted_service else [], "impacted_transactions": [impacted_transaction] if impacted_transaction else [], "source": dict(row)})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    ranked = ranked[:max(0, limit)]
    primary = ranked[0] if ranked else None
    return {"run_id": str(run_id), "primary": primary, "secondary": ranked[1:], "confidence": round(min(1.0, (primary["score"] / 100) if primary else 0.0), 3), "count": len(ranked)}

def identify_bottlenecks(rows, *, run_id=None, limit=10):
    analysis = analyze_bottlenecks(rows, run_id=str(run_id or ""), limit=limit)
    items = ([analysis["primary"]] if analysis.get("primary") else []) + analysis.get("secondary", [])
    return [dict(item["source"], bottleneck_score=item["score"],
                 impact="CRITICAL" if item["score"] >= 100 else "WARNING")
            for item in items]

def persist_bottleneck_intelligence(client: Any, analysis: Mapping[str, Any], *, measurement: str = "aiperf_bottleneck_intelligence") -> None:
    run_id = str(analysis["run_id"]); primary = analysis.get("primary") or {}
    secondaries = analysis.get("secondary") or []
    points = []
    for role, item in [("primary", primary), *[("secondary", x) for x in secondaries]]:
        points.append({"measurement": measurement, "tags": {"run_id": run_id, "role": role, "entity": str(item.get("entity", "NONE"))}, "fields": {"confidence": float(analysis.get("confidence", 0)), "reason": str(item.get("reason", "No bottleneck identified")), "impacted_services": ",".join(item.get("impacted_services", [])), "impacted_transactions": ",".join(item.get("impacted_transactions", [])), "score": float(item.get("score", 0))}})
    if points: client.write_points(points)

def summarize_bottlenecks(analysis: Mapping[str, Any]) -> dict[str, Any]:
    primary = analysis.get("primary")
    if not primary:
        return {
            "primary_bottleneck": "",
            "secondary_bottleneck": "",
            "confidence": 0.0,
            "reason": "No bottleneck identified from the available run evidence.",
            "impacted_services": [],
            "impacted_transactions": [],
        }
    secondary = analysis.get("secondary") or []
    return {
        "primary_bottleneck": primary["entity"],
        "secondary_bottleneck": secondary[0]["entity"] if secondary else "",
        "confidence": analysis.get("confidence", 0.0),
        "reason": primary["reason"],
        "impacted_services": primary.get("impacted_services", []),
        "impacted_transactions": primary.get("impacted_transactions", []),
    }

def query_bottlenecks(client: Any, run_id: str, measurements=("aiperf_transaction_comparison", "aiperf_service_comparison")):
    rows = []
    for measurement in measurements:
        rows.extend(client.query(f'SELECT * FROM "{measurement}" WHERE current_run_id=\'{run_id}\' OR run_id=\'{run_id}\'').get_points())
    return rows

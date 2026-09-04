"""Optional embeddings knowledge layer; all I/O is explicit and lazy."""
from __future__ import annotations

import json
import math
import os
from typing import Any


def create_embedding(text: str, *, api_url: str | None = None, api_key: str | None = None,
                     model: str | None = None, timeout: int = 30) -> list[float] | None:
    url = api_url or os.getenv("EMBEDDING_API_URL")
    key = api_key or os.getenv("EMBEDDING_API_KEY")
    model = model or os.getenv("EMBEDDING_MODEL", "text-embeddings")
    if not url or not key:
        return None
    import requests
    response = requests.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                             json={"model": model, "input": text}, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    vector = payload.get("data", [{}])[0].get("embedding")
    return [float(value) for value in vector] if vector else None


def store_finding(client: Any, run_id: str, text: str, vector: list[float],
                  *, risk_level: str = "UNKNOWN", recommendations: Any = None,
                  outcome: str = "UNKNOWN", measurement: str = "aiperf_embeddings") -> None:
    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).isoformat()
    client.write_points([{"measurement": measurement, "time": timestamp, "tags": {"run_id": str(run_id)},
                          "fields": {"findings_text": text, "embedding_vector": json.dumps(vector),
                                     "risk_level": risk_level, "timestamp": timestamp,
                                     "recommendations": json.dumps(recommendations or []),
                                     "outcome": outcome}}])

def package_to_text(package: Any) -> str:
    """Create stable, useful embedding input from a findings package."""
    if isinstance(package, str):
        return package
    if not isinstance(package, dict):
        return str(package)
    sections = (
        "executive_summary", "risk", "release_impact", "top_regressions",
        "top_improvements", "anomaly_summary", "anomalies",
        "bottleneck_intelligence", "bottleneck_summary",
        "correlation_intelligence", "correlation_summary",
        "recommended_actions",
    )
    return "\n".join(
        f"{key}: {package.get(key)}"
        for key in sections
        if key in package
    )


def load_findings(client: Any, *, measurement: str = "aiperf_embeddings") -> list[dict[str, Any]]:
    rows = []
    for row in client.query(f'SELECT * FROM "{measurement}"').get_points():
        try:
            row["embedding"] = json.loads(row.get("embedding_vector", "[]"))
        except (TypeError, ValueError):
            row["embedding"] = []
        rows.append(row)
    return rows


def cosine_similarity(left: list[float], right: list[float]) -> float:
    denominator = math.sqrt(sum(x*x for x in left) * sum(y*y for y in right))
    return sum(x*y for x, y in zip(left, right)) / denominator if denominator else 0.0

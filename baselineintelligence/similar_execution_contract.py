"""Canonical contract for similar-execution evidence."""
from __future__ import annotations
from typing import Any


def canonical_similar_execution(row: dict[str, Any], current_run_id: str | None = None) -> dict[str, Any]:
    """Normalize legacy similarity rows without exposing their metric payload."""
    return {
        "current_run_id": str(row.get("current_run_id", current_run_id or "UNKNOWN")),
        "similar_run_id": str(row.get("similar_run_id", "UNKNOWN")),
        "similarity_score": float(row.get("similarity_score", 0) or 0),
        "contract_version": "similar-execution.v1",
        "evidence_source": str(row.get("evidence_source", "aiperf_similar_execution")),
    }

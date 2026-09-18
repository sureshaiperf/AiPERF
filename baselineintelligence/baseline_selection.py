"""Authoritative approved-baseline and reference-run selection for AiPERF."""
from __future__ import annotations

import math
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

REGISTRY_MEASUREMENT = "aiperf_baseline_registry"
EXECUTION_MEASUREMENT = "aiperf_execution_history"
SELECTION_MEASUREMENT = "aiperf_baseline_selection"
_MEASUREMENT_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]+")


class BaselineSelectionError(RuntimeError):
    """Raised when configured approved-baseline governance cannot be satisfied."""


@dataclass(frozen=True)
class SelectionDecision:
    current_run_id: str
    comparison_run_id: str | None
    baseline_id: str | None
    approved_baseline_run_id: str | None
    selection_mode: str
    selection_status: str
    compatibility_status: str
    fallback_used: bool
    reason: str
    semantic_version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def safe_run_id(value: Any) -> str:
    """Validate and normalize an AiPERF execution identifier."""
    text = "" if value is None else f"{value}"
    text = text.strip()
    if not text:
        raise ValueError("RUN_ID is missing or empty")
    if not _RUN_ID_PATTERN.fullmatch(text):
        raise ValueError("RUN_ID contains unsupported characters")
    return text


def safe_measurement(value: Any) -> str:
    """Validate an InfluxDB measurement used in constructed InfluxQL."""
    text = "" if value is None else f"{value}"
    text = text.strip()
    if not text or not _MEASUREMENT_PATTERN.fullmatch(text):
        raise ValueError(f"Unsupported InfluxDB measurement name: {text}")
    return text


def esc(value: Any) -> str:
    """Escape a string literal for InfluxQL v1 queries."""
    text = "" if value is None else f"{value}"
    return text.replace("\\", "\\\\").replace("'", "\\'")


def points(client: Any, query: str) -> list[dict[str, Any]]:
    """Execute an InfluxQL query and return normalized point dictionaries."""
    result = client.query(query)
    return [dict(row) for row in result.get_points()]


def latest_execution(client: Any, run_id: str) -> dict[str, Any] | None:
    selected_run = safe_run_id(run_id)
    rows = points(
        client,
        (
            f'SELECT * FROM "{EXECUTION_MEASUREMENT}" '
            f'WHERE "run_id"=\'{esc(selected_run)}\' '
            "ORDER BY time DESC LIMIT 1"
        ),
    )
    return rows[0] if rows else None


def latest_approved_registry(
    client: Any,
    baseline_id: str | None,
    approved_run_id: str,
) -> dict[str, Any] | None:
    approved_run = safe_run_id(approved_run_id)
    if baseline_id:
        predicate = f'"baseline_id"=\'{esc(baseline_id.strip())}\''
    else:
        predicate = f'"representative_run_id"=\'{esc(approved_run)}\''
    rows = points(
        client,
        (
            f'SELECT * FROM "{REGISTRY_MEASUREMENT}" '
            f"WHERE {predicate} ORDER BY time DESC LIMIT 1"
        ),
    )
    return rows[0] if rows else None


def semantic_version(row: Mapping[str, Any]) -> int:
    raw = row.get(
        "throughput_semantic_version",
        row.get("semantic_version", 1),
    )
    try:
        value = float(raw)
        if not math.isfinite(value):
            return 1
        return max(1, int(value))
    except (TypeError, ValueError, OverflowError):
        return 1


def compatible(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> tuple[bool, str, int]:
    current_version = semantic_version(current)
    baseline_version = semantic_version(baseline)
    if current_version != baseline_version:
        return (
            False,
            "Throughput semantic version differs: "
            f"current={current_version}, baseline={baseline_version}",
            current_version,
        )
    for key in ("application", "environment", "test_name"):
        current_value = f"{current.get(key, '')}".strip()
        baseline_value = f"{baseline.get(key, '')}".strip()
        if current_value and baseline_value and current_value != baseline_value:
            return (
                False,
                f"{key} differs: current={current_value}, "
                f"baseline={baseline_value}",
                current_version,
            )
    return (
        True,
        "Execution semantic, application, environment and test identity "
        "are compatible.",
        current_version,
    )


def delete_current_run_series(
    client: Any,
    measurement: str,
    current_run_id: str,
) -> None:
    """Delete all existing comparison series for one current execution."""
    selected_measurement = safe_measurement(measurement)
    selected_run = safe_run_id(current_run_id)
    client.query(
        f'DROP SERIES FROM "{selected_measurement}" '
        f'WHERE "current_run_id"=\'{esc(selected_run)}\''
    )


def validate_single_comparison_target(
    client: Any,
    measurement: str,
    current_run_id: str,
    expected_comparison_run_id: str,
    expected_record_count: int | None = None,
) -> dict[str, Any]:
    """Confirm regenerated evidence contains exactly one comparison target."""
    selected_measurement = safe_measurement(measurement)
    selected_run = safe_run_id(current_run_id)
    expected_run = safe_run_id(expected_comparison_run_id)
    rows = points(
        client,
        (
            f'SELECT * FROM "{selected_measurement}" '
            f'WHERE "current_run_id"=\'{esc(selected_run)}\''
        ),
    )
    targets = sorted(
        {
            f"{row.get('comparison_run_id') or row.get('similar_run_id') or ''}".strip()
            for row in rows
            if f"{row.get('comparison_run_id') or row.get('similar_run_id') or ''}".strip()
        }
    )
    if targets != [expected_run]:
        raise RuntimeError(
            f"{selected_measurement} comparison-target validation failed for "
            f"{selected_run}. Expected [{expected_run}], received {targets}."
        )
    if expected_record_count is not None and len(rows) != expected_record_count:
        raise RuntimeError(
            f"{selected_measurement} record-count validation failed for "
            f"{selected_run}. Expected {expected_record_count}, "
            f"received {len(rows)}."
        )
    return {
        "measurement": selected_measurement,
        "current_run_id": selected_run,
        "comparison_run_id": expected_run,
        "record_count": len(rows),
        "target_count": len(targets),
        "status": "VALID",
    }


def persist_selection(client: Any, decision: SelectionDecision) -> None:
    """Persist one idempotent baseline-selection governance record."""
    delete_current_run_series(
        client,
        SELECTION_MEASUREMENT,
        decision.current_run_id,
    )
    point = {
        "measurement": SELECTION_MEASUREMENT,
        "tags": {
            "current_run_id": decision.current_run_id,
            "selection_mode": decision.selection_mode,
            "selection_status": decision.selection_status,
            "compatibility_status": decision.compatibility_status,
        },
        "fields": {
            "comparison_run_id": decision.comparison_run_id or "",
            "approved_baseline_run_id": (
                decision.approved_baseline_run_id or ""
            ),
            "baseline_id": decision.baseline_id or "",
            "fallback_used": int(decision.fallback_used),
            "reason": decision.reason,
            "semantic_version": int(decision.semantic_version or 0),
        },
    }
    if client.write_points([point]) is False:
        raise RuntimeError("InfluxDB rejected baseline-selection evidence")


def select_comparison_run(
    client: Any,
    current_run_id: str,
    candidates: Sequence[str],
) -> SelectionDecision:
    """Select an approved baseline or the latest compatible prior reference."""
    selected_run = safe_run_id(current_run_id)
    current = latest_execution(client, selected_run)
    if not current:
        raise BaselineSelectionError(
            f"CURRENT_RUN_UNAVAILABLE: {selected_run}"
        )

    approved_value = os.getenv("AIPERF_APPROVED_BASELINE_RUN_ID", "")
    approved = approved_value.strip()
    baseline_id_value = os.getenv("AIPERF_BASELINE_ID", "")
    baseline_id = baseline_id_value.strip() or None

    if approved:
        approved = safe_run_id(approved)
        if approved == selected_run:
            raise BaselineSelectionError(
                "APPROVED_BASELINE_IS_CURRENT_RUN: no comparison was created"
            )
        registry = latest_approved_registry(client, baseline_id, approved)
        if not registry:
            raise BaselineSelectionError(
                "APPROVED_BASELINE_REGISTRY_UNAVAILABLE: "
                "no registry record exists"
            )
        registry_status = f"{registry.get('status', '')}".strip().upper()
        if registry_status != "APPROVED":
            raise BaselineSelectionError(
                "APPROVED_BASELINE_NOT_APPROVED: "
                f"status={registry_status or 'UNKNOWN'}"
            )
        registered_run = safe_run_id(
            registry.get("representative_run_id")
        )
        if registered_run != approved:
            raise BaselineSelectionError(
                "APPROVED_BASELINE_REGISTRY_MISMATCH: "
                f"configured={approved}, registered={registered_run}"
            )
        baseline = latest_execution(client, approved)
        if not baseline:
            raise BaselineSelectionError(
                f"APPROVED_BASELINE_UNAVAILABLE: {approved}"
            )
        is_compatible, reason, version = compatible(current, baseline)
        if not is_compatible:
            raise BaselineSelectionError(
                f"APPROVED_BASELINE_INCOMPATIBLE: {reason}"
            )
        registered_baseline_id = f"{registry.get('baseline_id') or ''}".strip()
        decision = SelectionDecision(
            current_run_id=selected_run,
            comparison_run_id=approved,
            baseline_id=baseline_id or registered_baseline_id or None,
            approved_baseline_run_id=approved,
            selection_mode="APPROVED_BASELINE",
            selection_status="APPROVED_BASELINE_SELECTED",
            compatibility_status="COMPATIBLE",
            fallback_used=False,
            reason=reason,
            semantic_version=version,
        )
        persist_selection(client, decision)
        return decision

    ordered_candidates: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not item:
            continue
        candidate = safe_run_id(item)
        if candidate == selected_run or candidate in seen:
            continue
        seen.add(candidate)
        ordered_candidates.append(candidate)

    for candidate in reversed(ordered_candidates):
        baseline = latest_execution(client, candidate)
        if not baseline:
            continue
        is_compatible, reason, version = compatible(current, baseline)
        if not is_compatible:
            continue
        decision = SelectionDecision(
            current_run_id=selected_run,
            comparison_run_id=candidate,
            baseline_id=None,
            approved_baseline_run_id=None,
            selection_mode="REFERENCE_ONLY",
            selection_status="REFERENCE_FALLBACK_SELECTED",
            compatibility_status="COMPATIBLE",
            fallback_used=True,
            reason=reason,
            semantic_version=version,
        )
        persist_selection(client, decision)
        return decision

    raise BaselineSelectionError(
        "REFERENCE_UNAVAILABLE: no prior compatible execution exists"
    )

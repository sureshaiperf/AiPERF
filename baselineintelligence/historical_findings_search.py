"""Search persisted AiPERF findings by RUN_ID, text, or embeddings.

The module also enriches similar historical findings with observed release
outcomes from the ``aiperf_release_outcome`` measurement when available.
It does not call an LLM.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any, Iterable, Mapping, Sequence

try:
    from .embeddings_knowledge_layer import cosine_similarity, load_findings
except ImportError:
    from embeddings_knowledge_layer import cosine_similarity, load_findings

LOGGER = logging.getLogger(__name__)

FINDINGS_MEASUREMENT = os.getenv(
    "AIPERF_FINDINGS_MEASUREMENT", "aiperf_findings_package"
)
OUTCOME_MEASUREMENT = os.getenv(
    "AIPERF_RELEASE_OUTCOME_MEASUREMENT", "aiperf_release_outcome"
)
SIMILARITY_MEASUREMENT = os.getenv(
    "AIPERF_HISTORICAL_SIMILARITY_MEASUREMENT",
    "aiperf_historical_similarity",
)
MAX_LIMIT = max(1, int(os.getenv("AIPERF_HISTORICAL_MAX_LIMIT", "100")))
MIN_SIMILARITY = float(
    os.getenv("AIPERF_MIN_HISTORICAL_SIMILARITY", "0.0")
)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    result = str(value).strip()
    return result if result else default


def _number(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _boolean(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off", ""}:
        return False
    return default


def _safe_run_id(value: Any) -> str:
    run_id = _text(value)
    if not run_id:
        raise ValueError("RUN_ID is required")
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", run_id):
        raise ValueError("RUN_ID contains unsupported characters")
    return run_id


def _bounded_limit(value: Any, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return min(max(result, 0), MAX_LIMIT)


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list, tuple, int, float, bool)):
        return value
    text = str(value).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _as_list(value: Any) -> list[Any]:
    parsed = _json_value(value, value)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, tuple):
        return list(parsed)
    if isinstance(parsed, str):
        return [parsed] if parsed.strip() else []
    return [parsed]


def _as_dict(value: Any) -> dict[str, Any]:
    parsed = _json_value(value, {})
    return dict(parsed) if isinstance(parsed, dict) else {}


def _embedding(value: Any) -> list[float]:
    parsed = _json_value(value, value)
    if not isinstance(parsed, (list, tuple)):
        return []
    vector: list[float] = []
    for element in parsed:
        number = _number(element, float("nan"))
        if not math.isfinite(number):
            return []
        vector.append(number)
    return vector


def _similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    try:
        score = float(cosine_similarity(list(left), list(right)))
    except Exception as exc:
        LOGGER.warning("Cosine similarity failed: %s", exc)
        return 0.0
    if not math.isfinite(score):
        return 0.0
    return round(max(-1.0, min(1.0, score)), 6)


def _query_points(client: Any, query: str) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in client.query(query).get_points()]
    except Exception as exc:
        LOGGER.warning("Optional InfluxDB query failed: %s", exc)
        return []


def _load_rows(client: Any) -> list[dict[str, Any]]:
    try:
        rows = [dict(row) for row in (load_findings(client) or [])]
    except Exception as exc:
        LOGGER.warning("Unable to load embedded findings: %s", exc)
        rows = []

    if rows:
        return rows

    rows = _query_points(client, f'SELECT * FROM "{FINDINGS_MEASUREMENT}"')
    normalized: list[dict[str, Any]] = []
    for row in rows:
        package = _as_dict(row.get("findings_json"))
        metadata = _as_dict(package.get("metadata"))
        risk = _as_dict(package.get("risk") or package.get("release_risk"))
        row["run_id"] = _text(
            row.get("run_id") or package.get("run_id") or metadata.get("run_id")
        )
        row["summary"] = _text(
            row.get("summary")
            or package.get("summary")
            or package.get("executive_summary")
        )
        row["text"] = _text(
            row.get("text") or row.get("summary") or row.get("findings_json")
        )
        row["risk_level"] = _text(
            row.get("risk_level") or risk.get("level"), "UNKNOWN"
        )
        row["recommendations"] = (
            row.get("recommendations") or package.get("recommendations") or []
        )
        row["embedding"] = _embedding(row.get("embedding"))
        normalized.append(row)
    return normalized


def _deduplicate(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        row = dict(raw)
        key = _text(row.get("run_id")) or json.dumps(
            row, sort_keys=True, default=str
        )
        if key not in seen:
            seen.add(key)
            output.append(row)
    return output


def _outcome_label(value: Any) -> str:
    normalized = _text(value, "NOT_OBSERVED").upper()
    aliases = {
        "PASS": "SUCCESS",
        "PASSED": "SUCCESS",
        "GO": "SUCCESS",
        "STABLE": "SUCCESS",
        "SUCCESSFUL": "SUCCESS",
        "FAIL": "FAILURE",
        "FAILED": "FAILURE",
        "NO_GO": "FAILURE",
        "NOGO": "FAILURE",
        "ROLLBACK": "FAILURE",
        "ROLLED_BACK": "FAILURE",
        "UNKNOWN": "NOT_OBSERVED",
        "UNAVAILABLE": "NOT_OBSERVED",
        "NONE": "NOT_OBSERVED",
    }
    normalized = aliases.get(normalized, normalized)
    allowed = {"SUCCESS", "FAILURE", "PARTIAL_SUCCESS", "NOT_OBSERVED"}
    return normalized if normalized in allowed else "NOT_OBSERVED"


def _production_status(value: Any) -> str:
    normalized = _text(value, "NOT_OBSERVED").upper()
    aliases = {
        "HEALTHY": "STABLE",
        "NORMAL": "STABLE",
        "PASS": "STABLE",
        "FAIL": "UNSTABLE",
        "FAILED": "UNSTABLE",
        "UNKNOWN": "NOT_OBSERVED",
        "UNAVAILABLE": "NOT_OBSERVED",
        "NONE": "NOT_OBSERVED",
    }
    normalized = aliases.get(normalized, normalized)
    allowed = {"STABLE", "DEGRADED", "UNSTABLE", "ROLLED_BACK", "NOT_OBSERVED"}
    return normalized if normalized in allowed else "NOT_OBSERVED"


def _release_decision(value: Any) -> str:
    normalized = _text(value, "UNKNOWN").upper()
    aliases = {
        "GO": "PROCEED",
        "PASS": "PROCEED",
        "APPROVED": "PROCEED",
        "CONDITIONAL_GO": "CONDITIONAL",
        "REVIEW": "CONDITIONAL",
        "NO_GO": "BLOCK",
        "NOGO": "BLOCK",
        "FAIL": "BLOCK",
        "REJECTED": "BLOCK",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {"PROCEED", "CONDITIONAL", "BLOCK", "UNKNOWN"} else "UNKNOWN"


def _normalize_outcome(row: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(row or {})
    rollback = _boolean(source.get("rollback_flag") or source.get("rolled_back"))
    outcome = _outcome_label(
        source.get("outcome") or source.get("outcomes") or source.get("outcome_label")
    )
    status = _production_status(
        source.get("production_status") or source.get("prod_status") or source.get("status")
    )
    if rollback:
        outcome = "FAILURE"
        if status == "NOT_OBSERVED":
            status = "ROLLED_BACK"
    return {
        "outcome": outcome,
        "production_status": status,
        "release_decision": _release_decision(
            source.get("release_decision") or source.get("decision")
        ),
        "incident_count": max(
            0,
            _integer(
                source.get("incident_count")
                or source.get("production_incident_count"),
                0,
            ),
        ),
        "rollback_flag": rollback,
        "production_p95": _number(source.get("production_p95")),
        "production_error_rate": _number(
            source.get("production_error_rate")
            or source.get("production_error_pct")
        ),
        "observation_window_days": max(
            0, _integer(source.get("observation_window_days"), 0)
        ),
        "observed_at": _text(source.get("observed_at") or source.get("time")),
        "notes": _text(source.get("notes")),
        "outcome_observed": outcome != "NOT_OBSERVED",
    }


def _load_outcomes(client: Any) -> dict[str, dict[str, Any]]:
    rows = _query_points(
        client, f'SELECT * FROM "{OUTCOME_MEASUREMENT}" ORDER BY time DESC'
    )
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        run_id = _text(
            row.get("run_id") or row.get("execution_run_id") or row.get("test_run_id")
        )
        if run_id and run_id not in output:
            output[run_id] = _normalize_outcome(row)
    return output


def _embedded_outcome(row: Mapping[str, Any]) -> dict[str, Any]:
    return _normalize_outcome(row)


def _recommendations(value: Any) -> list[Any]:
    return [item.strip() if isinstance(item, str) else item for item in _as_list(value) if item is not None and (not isinstance(item, str) or item.strip())]


def _searchable_text(row: Mapping[str, Any]) -> str:
    direct = _text(row.get("text") or row.get("summary") or row.get("findings_json"))
    if direct:
        return direct
    return " ".join(
        part
        for part in (
            _text(row.get("run_id")),
            _text(row.get("risk_level") or row.get("risk")),
            _text(row.get("outcome") or row.get("outcomes")),
            json.dumps(_recommendations(row.get("recommendations")), default=str),
        )
        if part
    )


def search_findings(
    client: Any,
    query: str,
    *,
    run_id: str | None = None,
    limit: int = 10,
    query_vector: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Search findings using RUN_ID, lexical terms, or an embedding."""

    result_limit = _bounded_limit(limit, 10)
    if result_limit == 0:
        return []

    expected_run_id = _safe_run_id(run_id) if run_id is not None else None
    terms = list(dict.fromkeys(re.findall(r"[A-Za-z0-9_.:/-]+", _text(query).lower())))
    vector = _embedding(query_vector)
    outcomes = _load_outcomes(client)
    matches: list[dict[str, Any]] = []

    for row in _deduplicate(_load_rows(client)):
        row_run_id = _text(row.get("run_id"))
        if expected_run_id is not None and row_run_id != expected_run_id:
            continue

        text = _searchable_text(row)
        lexical = (
            sum(term in text.lower() for term in terms) / len(terms)
            if terms
            else 0.0
        )
        row_vector = _embedding(row.get("embedding"))
        semantic = max(0.0, _similarity(vector, row_vector))

        if terms and vector and row_vector:
            relevance = semantic * 0.60 + lexical * 0.40
        elif vector and row_vector:
            relevance = semantic
        elif terms:
            relevance = lexical
        else:
            relevance = 1.0 if expected_run_id else 0.0

        if relevance <= 0.0:
            continue

        item = dict(row)
        item.update(outcomes.get(row_run_id) or _embedded_outcome(row))
        item["run_id"] = row_run_id
        item["text"] = text
        item["embedding"] = row_vector
        item["recommendations"] = _recommendations(row.get("recommendations"))
        item["lexical_score"] = round(lexical, 6)
        item["semantic_score"] = round(semantic, 6)
        item["relevance"] = round(relevance, 6)
        item["relevance_pct"] = round(relevance * 100.0, 2)
        matches.append(item)

    matches.sort(
        key=lambda item: (
            _number(item.get("relevance")),
            _number(item.get("semantic_score")),
            _text(item.get("time")),
        ),
        reverse=True,
    )
    return matches[:result_limit]


def _persist_similarity(
    client: Any, run_id: str, results: Sequence[Mapping[str, Any]]
) -> None:
    if not results:
        return

    try:
        client.query(
            f'DELETE FROM "{SIMILARITY_MEASUREMENT}" WHERE run_id=\'{run_id}\''
        )
    except Exception as exc:
        LOGGER.warning("Could not delete previous similarity rows: %s", exc)

    points: list[dict[str, Any]] = []
    for rank, row in enumerate(results, start=1):
        points.append(
            {
                "measurement": SIMILARITY_MEASUREMENT,
                "tags": {
                    "run_id": run_id,
                    "similar_run_id": _text(row.get("run_id"), "UNKNOWN"),
                },
                "fields": {
                    "rank": rank,
                    "similarity_score": float(_number(row.get("similarity_score"))),
                    "similarity_pct": float(_number(row.get("similarity_pct"))),
                    "risk": _text(row.get("risk"), "UNKNOWN"),
                    "recommendations": json.dumps(
                        _recommendations(row.get("recommendations")),
                        ensure_ascii=False,
                        default=str,
                    ),
                    "outcome": _outcome_label(row.get("outcome")),
                    "production_status": _production_status(
                        row.get("production_status")
                    ),
                    "release_decision": _release_decision(
                        row.get("release_decision")
                    ),
                    "incident_count": max(0, _integer(row.get("incident_count"))),
                    "rollback_flag": _boolean(row.get("rollback_flag")),
                    "outcome_observed": _boolean(row.get("outcome_observed")),
                },
            }
        )

    try:
        written = client.write_points(points)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to persist historical similarity for RUN_ID {run_id}"
        ) from exc
    if written is False:
        raise RuntimeError(
            f"InfluxDB rejected historical similarity for RUN_ID {run_id}"
        )


def find_similar_findings(
    client: Any,
    run_id: str,
    *,
    limit: int = 5,
    query_vector: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Return similar historical findings enriched with release outcomes."""

    current_run_id = _safe_run_id(run_id)
    result_limit = _bounded_limit(limit, 5)
    if result_limit == 0:
        return []

    rows = _deduplicate(_load_rows(client))
    current = next(
        (row for row in rows if _text(row.get("run_id")) == current_run_id),
        None,
    )
    vector = _embedding(query_vector) or _embedding(
        (current or {}).get("embedding")
    )
    if not vector:
        LOGGER.warning(
            "No embedding available for RUN_ID %s; similarity search skipped",
            current_run_id,
        )
        return []

    outcomes = _load_outcomes(client)
    results: list[dict[str, Any]] = []

    for row in rows:
        candidate_run_id = _text(row.get("run_id"))
        if not candidate_run_id or candidate_run_id == current_run_id:
            continue

        candidate_vector = _embedding(row.get("embedding"))
        if not candidate_vector:
            continue

        score = _similarity(vector, candidate_vector)
        if score < MIN_SIMILARITY:
            continue

        item = dict(row)
        item.update(outcomes.get(candidate_run_id) or _embedded_outcome(row))
        item["run_id"] = candidate_run_id
        item["similarity_score"] = score
        item["similarity_pct"] = round(max(0.0, score) * 100.0, 2)
        item["risk"] = _text(
            row.get("risk_level") or row.get("risk"), "UNKNOWN"
        )
        item["recommendations"] = _recommendations(row.get("recommendations"))
        item["outcomes"] = item["outcome"]  # backward compatibility
        results.append(item)

    results.sort(
        key=lambda item: (
            _number(item.get("similarity_score")),
            _boolean(item.get("outcome_observed")),
            _text(item.get("time")),
        ),
        reverse=True,
    )
    results = results[:result_limit]
    for rank, item in enumerate(results, start=1):
        item["rank"] = rank

    _persist_similarity(client, current_run_id, results)
    return results


def summarize_similar_outcomes(
    similar_findings: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize observed outcomes among similar historical runs."""

    rows = [dict(row) for row in similar_findings]
    successful = sum(_outcome_label(row.get("outcome")) == "SUCCESS" for row in rows)
    failed = sum(_outcome_label(row.get("outcome")) == "FAILURE" for row in rows)
    partial = sum(
        _outcome_label(row.get("outcome")) == "PARTIAL_SUCCESS" for row in rows
    )
    not_observed = sum(
        _outcome_label(row.get("outcome")) == "NOT_OBSERVED" for row in rows
    )
    observed = successful + failed + partial
    success_rate = successful / observed * 100.0 if observed else 0.0
    weighted_rate = (
        (successful + 0.5 * partial) / observed * 100.0 if observed else 0.0
    )
    return {
        "total_similar_matches": len(rows),
        "observed_matches": observed,
        "successful_matches": successful,
        "failed_matches": failed,
        "partial_success_matches": partial,
        "not_observed_matches": not_observed,
        "rollback_count": sum(_boolean(row.get("rollback_flag")) for row in rows),
        "incident_count": sum(
            max(0, _integer(row.get("incident_count"))) for row in rows
        ),
        "success_rate": round(success_rate, 2),
        "weighted_success_rate": round(weighted_rate, 2),
        "outcome_confidence": (
            "HIGH" if observed >= 5 else "MEDIUM" if observed >= 3 else "LOW"
        ),
        "basis": (
            "Rates use only similar historical runs with observed outcomes."
        ),
    }


__all__ = [
    "search_findings",
    "find_similar_findings",
    "summarize_similar_outcomes",
]

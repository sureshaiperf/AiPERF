"""Optional, production-ready embeddings knowledge layer for AiPERF.

All network and database I/O is explicit and lazy. The module creates bounded
embedding input, calls an OpenAI-compatible embeddings endpoint, validates the
response, persists one finding per RUN_ID, and loads historical vectors safely.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def _load_local_environment() -> None:
    """Load a local .env file without overriding injected environment values."""
    if load_dotenv is None:
        return
    module_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(module_dir, ".env")
    load_dotenv(dotenv_path=dotenv_path, override=False)


_load_local_environment()

LOGGER = logging.getLogger(__name__)

DEFAULT_MEASUREMENT = os.getenv("AIPERF_EMBEDDING_MEASUREMENT", "aiperf_embeddings")
DEFAULT_MODEL = os.getenv("EMBEDDING_MODEL", "text-embeddings")
DEFAULT_TIMEOUT = int(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "45"))
DEFAULT_MAX_CHARS = int(os.getenv("AIPERF_EMBEDDING_MAX_CHARS", "12000"))
DEFAULT_MAX_FINDINGS = int(os.getenv("AIPERF_EMBEDDING_LOAD_LIMIT", "1000"))
EXPECTED_DIMENSIONS = int(os.getenv("AIPERF_EMBEDDING_DIMENSIONS", "736"))


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    result = str(value).strip()
    return result if result else default


def _safe_run_id(value: Any) -> str:
    run_id = _text(value)
    if not run_id:
        raise ValueError("RUN_ID is required")
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", run_id):
        raise ValueError("RUN_ID contains unsupported characters")
    return run_id


def _escape_influx_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _finite_vector(value: Any) -> list[float]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(value, (list, tuple)):
        return []
    vector: list[float] = []
    for element in value:
        try:
            number = float(element)
        except (TypeError, ValueError, OverflowError):
            return []
        if not math.isfinite(number):
            return []
        vector.append(number)
    return vector


def _bounded_embedding_text(text: Any, max_chars: int) -> str:
    """Normalize and bound embedding input without breaking JSON payloads."""
    normalized = _text(text)
    if not normalized:
        raise ValueError("Embedding input is empty")
    normalized = normalized.replace("\x00", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")
    if len(normalized) <= max_chars:
        return normalized

    marker = "\n[TRUNCATED FOR EMBEDDING]\n"
    head_size = int((max_chars - len(marker)) * 0.75)
    tail_size = max_chars - len(marker) - head_size
    LOGGER.warning(
        "Embedding input truncated from %s to %s characters",
        len(normalized),
        max_chars,
    )
    return normalized[:head_size] + marker + normalized[-tail_size:]


def _response_error(response: Any) -> RuntimeError:
    body = _text(getattr(response, "text", ""))
    if len(body) > 2000:
        body = body[:2000] + "..."
    status = getattr(response, "status_code", "UNKNOWN")
    return RuntimeError(f"Embedding API returned HTTP {status}: {body or 'empty response'}")


def _extract_vector(payload: Any) -> list[float]:
    """Extract one vector from supported embedding API response formats."""
    direct_vector = _finite_vector(payload)
    if direct_vector:
        return direct_vector
    if isinstance(payload, list) and payload:
        first_vector = _finite_vector(payload[0])
        if first_vector:
            return first_vector
    if not isinstance(payload, Mapping):
        return []

    embeddings = payload.get("embeddings")

    # Coforge router response: {"embeddings": [0.1, 0.2, ...]}
    vector = _finite_vector(embeddings)
    if vector:
        return vector

    # Batched router response: {"embeddings": [[0.1, 0.2, ...]]}
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, Mapping):
            vector = _finite_vector(
                first.get("embedding")
                or first.get("vector")
                or first.get("values")
            )
        else:
            vector = _finite_vector(first)
        if vector:
            return vector

    # OpenAI-compatible response: {"data": [{"embedding": [...]}]}
    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, Mapping):
            vector = _finite_vector(
                first.get("embedding")
                or first.get("vector")
                or first.get("values")
            )
            if vector:
                return vector

    # Direct response: {"embedding": [...]}, {"vector": [...]}, or {"values": [...]}
    for key in ("embedding", "vector", "values"):
        vector = _finite_vector(payload.get(key))
        if vector:
            return vector

    # Nested response: {"result": {"embedding": [...]}}
    result = payload.get("result")
    if isinstance(result, Mapping):
        for key in ("embedding", "vector", "values"):
            vector = _finite_vector(result.get(key))
            if vector:
                return vector

    return []


def create_embedding(
    text: str,
    *,
    api_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[float] | None:
    """Create one embedding using the configured OpenAI-compatible endpoint.

    Returns ``None`` only when the endpoint or key is not configured. API,
    payload, and response failures raise descriptive exceptions so Jenkins can
    surface the actual server response.
    """
    url = _text(api_url or os.getenv("EMBEDDING_API_URL"))
    key = _text(api_key or os.getenv("EMBEDDING_API_KEY"))
    selected_model = _text(model or DEFAULT_MODEL)

    if not url or not key:
        LOGGER.warning("Embedding API URL or key is not configured")
        return None
    if not selected_model:
        raise ValueError("EMBEDDING_MODEL is required")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    bounded_text = _bounded_embedding_text(text, max_chars)
    payload = {"model": selected_model, "input": [bounded_text]}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    import requests

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise RuntimeError(f"Embedding API timed out after {timeout} seconds") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Embedding API request failed: {exc}") from exc

    if not 200 <= response.status_code < 300:
        raise _response_error(response)

    try:
        response_payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Embedding API returned non-JSON content") from exc

    vector = _extract_vector(response_payload)
    if not vector:
        keys = sorted(response_payload.keys()) if isinstance(response_payload, dict) else []
        raise RuntimeError(
            "Embedding API response did not contain a valid numeric vector; "
            f"top-level keys={keys}"
        )

    if EXPECTED_DIMENSIONS > 0 and len(vector) != EXPECTED_DIMENSIONS:
        raise RuntimeError(
            "Embedding dimension validation failed: "
            f"expected={EXPECTED_DIMENSIONS}, actual={len(vector)}"
        )

    LOGGER.info(
        "Embedding created successfully: model=%s dimensions=%s input_chars=%s",
        selected_model,
        len(vector),
        len(bounded_text),
    )
    return vector


def store_finding(
    client: Any,
    run_id: str,
    text: str,
    vector: list[float],
    *,
    risk_level: str = "UNKNOWN",
    recommendations: Any = None,
    outcome: str = "NOT_OBSERVED",
    measurement: str = DEFAULT_MEASUREMENT,
) -> None:
    """Upsert one embedded finding for a RUN_ID and verify persistence."""
    if client is None:
        raise ValueError("InfluxDB client is required")
    safe_run_id = _safe_run_id(run_id)
    finding_text = _text(text)
    if not finding_text:
        raise ValueError("Finding text is required")
    embedding = _finite_vector(vector)
    if not embedding:
        raise ValueError("A valid non-empty embedding vector is required")

    if recommendations is None:
        normalized_recommendations: list[Any] = []
    elif isinstance(recommendations, list):
        normalized_recommendations = recommendations
    elif isinstance(recommendations, tuple):
        normalized_recommendations = list(recommendations)
    else:
        normalized_recommendations = [recommendations]

    timestamp = datetime.now(timezone.utc).isoformat()
    content_hash = hashlib.sha256(finding_text.encode("utf-8")).hexdigest()

    try:
        client.query(
            f'DELETE FROM "{measurement}" '
            f"WHERE run_id='{_escape_influx_string(safe_run_id)}'"
        )
    except Exception as exc:
        LOGGER.warning("Could not remove prior embedding for %s: %s", safe_run_id, exc)

    point = {
        "measurement": measurement,
        "time": timestamp,
        "tags": {
            "run_id": safe_run_id,
            "risk_level": _text(risk_level, "UNKNOWN").upper(),
            "outcome": _text(outcome, "NOT_OBSERVED").upper(),
        },
        "fields": {
            "findings_text": finding_text,
            "text": finding_text,
            "embedding_vector": json.dumps(embedding, separators=(",", ":")),
            "embedding_dimensions": len(embedding),
            "content_hash": content_hash,
            "timestamp": timestamp,
            "recommendations": json.dumps(
                normalized_recommendations,
                ensure_ascii=False,
                default=str,
            ),
        },
    }

    try:
        written = client.write_points([point])
    except Exception as exc:
        raise RuntimeError(f"Failed to store embedding for RUN_ID {safe_run_id}") from exc
    if written is False:
        raise RuntimeError(f"InfluxDB rejected embedding for RUN_ID {safe_run_id}")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def package_to_text(package: Any) -> str:
    """Create compact, deterministic semantic input from a findings package."""
    if isinstance(package, str):
        return _bounded_embedding_text(package, DEFAULT_MAX_CHARS)
    if not isinstance(package, Mapping):
        return _bounded_embedding_text(str(package), DEFAULT_MAX_CHARS)

    sections = (
        "executive_summary",
        "risk",
        "release_impact",
        "top_regressions",
        "top_improvements",
        "anomaly_summary",
        "bottleneck_summary",
        "correlation_summary",
        "recommended_actions",
        "service_health",
        "observed_release_outcome",
    )
    lines = [f"run_id: {_text(package.get('run_id'), 'UNKNOWN')}"]
    for key in sections:
        if key not in package or package.get(key) in (None, "", [], {}):
            continue
        value = package.get(key)
        rendered = value if isinstance(value, str) else _stable_json(value)
        lines.append(f"{key}: {rendered}")
    return _bounded_embedding_text("\n".join(lines), DEFAULT_MAX_CHARS)


def load_findings(
    client: Any,
    *,
    measurement: str = DEFAULT_MEASUREMENT,
    limit: int = DEFAULT_MAX_FINDINGS,
) -> list[dict[str, Any]]:
    """Load latest unique embedded findings with validated numeric vectors."""
    if client is None:
        raise ValueError("InfluxDB client is required")
    bounded_limit = min(max(int(limit), 0), DEFAULT_MAX_FINDINGS)
    if bounded_limit == 0:
        return []

    query = f'SELECT * FROM "{measurement}" ORDER BY time DESC LIMIT {bounded_limit}'
    try:
        points = client.query(query).get_points()
    except Exception as exc:
        raise RuntimeError(f"Failed to load findings from {measurement}") from exc

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in points:
        row = dict(raw)
        run_id = _text(row.get("run_id"))
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        row["embedding"] = _finite_vector(row.get("embedding_vector"))
        row["text"] = _text(row.get("text") or row.get("findings_text"))
        try:
            row["recommendations"] = json.loads(
                _text(row.get("recommendations"), "[]")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            row["recommendations"] = []
        rows.append(row)
    return rows


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity for equal-length finite vectors."""
    left_vector = _finite_vector(left)
    right_vector = _finite_vector(right)
    if not left_vector or not right_vector:
        return 0.0
    if len(left_vector) != len(right_vector):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left_vector))
    right_norm = math.sqrt(sum(value * value for value in right_vector))
    denominator = left_norm * right_norm
    if denominator == 0.0:
        return 0.0
    score = sum(x * y for x, y in zip(left_vector, right_vector)) / denominator
    return max(-1.0, min(1.0, score)) if math.isfinite(score) else 0.0


__all__ = [
    "create_embedding",
    "store_finding",
    "package_to_text",
    "load_findings",
    "cosine_similarity",
]

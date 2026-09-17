"""Collect run-scoped Spring Boot Actuator metrics for AiPERF.

Designed for InfluxDB OSS 1.8.x using the Python ``influxdb`` client.
The ``request_count_available`` field is always written as an integer (0/1)
to preserve the existing InfluxDB field contract.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests
from influxdb import InfluxDBClient


LOG = logging.getLogger("aiperf.actuator")
logging.basicConfig(
    level=os.getenv("AIPERF_LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)

INFLUX_HOST = os.getenv("INFLUX_HOST", "localhost")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DATABASE", os.getenv("INFLUX_DB", "jmeter"))
INFLUX_USER = os.getenv("INFLUX_USER") or None
INFLUX_PASSWORD = os.getenv("INFLUX_PASSWORD") or None
INFLUX_TIMEOUT_SECONDS = int(os.getenv("INFLUX_TIMEOUT_SECONDS", "15"))

HTTP_TIMEOUT_SECONDS = float(os.getenv("AIPERF_HTTP_TIMEOUT_SECONDS", "10"))
VERIFY_TLS = os.getenv("AIPERF_VERIFY_TLS", "true").strip().lower() in {
    "1",
    "true",
    "yes",
}
MIN_SERVICE_REQUESTS = int(os.getenv("AIPERF_MIN_SERVICE_REQUESTS", "1"))

RUN_ID = (os.getenv("RUN_ID") or "").strip()
PHASE = (os.getenv("AIPERF_SERVICE_PHASE") or "after").strip().lower()

if not RUN_ID:
    raise RuntimeError("RUN_ID environment variable is required")
if not re.fullmatch(r"[A-Za-z0-9_.:-]+", RUN_ID):
    raise ValueError("RUN_ID contains unsupported characters")
if PHASE not in {"before", "after"}:
    raise ValueError("AIPERF_SERVICE_PHASE must be 'before' or 'after'")
if MIN_SERVICE_REQUESTS < 0:
    raise ValueError("AIPERF_MIN_SERVICE_REQUESTS cannot be negative")

SERVICES: Dict[str, Dict[str, Any]] = {
    "gateway": {
        "url": os.getenv("AIPERF_GATEWAY_URL", "http://localhost:8090"),
        "paths": ("/users", "/products", "/orders"),
    },
    "user-service": {
        "url": os.getenv("AIPERF_USER_SERVICE_URL", "http://localhost:8081"),
        "paths": ("/users",),
    },
    "product-service": {
        "url": os.getenv("AIPERF_PRODUCT_SERVICE_URL", "http://localhost:8082"),
        "paths": ("/products",),
    },
    "order-service": {
        "url": os.getenv("AIPERF_ORDER_SERVICE_URL", "http://localhost:8083"),
        "paths": ("/orders",),
    },
}

EXCLUDED_URI_TOKENS = ("/actuator", "management", "prometheus")
EXCLUDED_URI_VALUES = {
    "UNKNOWN",
    "NOT_FOUND",
    "REDIRECTION",
    "root",
    "**",
}


@dataclass(frozen=True)
class CounterSnapshot:
    count: float = 0.0
    total_time_ms: float = 0.0
    max_time_ms: float = 0.0
    matched_uris: Tuple[str, ...] = ()
    available: bool = False


SESSION = requests.Session()
SESSION.headers.update(
    {
        "Accept": "application/json",
        "User-Agent": "AiPERF-Actuator-Collector/2.1",
    }
)

CLIENT = InfluxDBClient(
    host=INFLUX_HOST,
    port=INFLUX_PORT,
    username=INFLUX_USER,
    password=INFLUX_PASSWORD,
    database=INFLUX_DB,
    timeout=INFLUX_TIMEOUT_SECONDS,
)


def integer_flag(value: Any) -> int:
    """Return an InfluxDB-safe integer flag, never a Boolean."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return 1
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return 0
    try:
        number = float(value)
        return 1 if math.isfinite(number) and number != 0.0 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def finite_nonnegative(value: Any) -> float:
    """Return a finite non-negative float rounded for stable storage."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return round(max(0.0, number), 4)


def escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def fetch_metric(
    base_url: str,
    metric_name: str,
    tags: Optional[Iterable[Tuple[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/actuator/metrics/{metric_name}"
    if tags:
        query = "&".join(
            f"tag={quote(str(key), safe='')}:{quote(str(value), safe='')}"
            for key, value in tags
        )
        url = f"{url}?{query}"

    try:
        response = SESSION.get(
            url,
            timeout=HTTP_TIMEOUT_SECONDS,
            verify=VERIFY_TLS,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (requests.RequestException, ValueError) as exc:
        LOG.warning(
            "Metric unavailable: service=%s metric=%s error=%s",
            base_url,
            metric_name,
            exc,
        )
        return None


def measurement_value(
    payload: Optional[Dict[str, Any]],
    statistic: str,
) -> float:
    for item in (payload or {}).get("measurements", []):
        if str(item.get("statistic", "")).upper() != statistic.upper():
            continue
        try:
            value = float(item.get("value") or 0.0)
            return value if math.isfinite(value) else 0.0
        except (TypeError, ValueError, OverflowError):
            return 0.0
    return 0.0


def uri_values(payload: Optional[Dict[str, Any]]) -> List[str]:
    for tag in (payload or {}).get("availableTags", []):
        if tag.get("tag") == "uri":
            return [str(value) for value in tag.get("values", [])]
    return []


def is_workload_uri(uri: str, prefixes: Tuple[str, ...]) -> bool:
    normalized = uri.strip()
    lowered = normalized.lower()
    if not normalized or normalized in EXCLUDED_URI_VALUES:
        return False
    if any(token in lowered for token in EXCLUDED_URI_TOKENS):
        return False
    return any(
        normalized == prefix
        or normalized.startswith(prefix + "/")
        or normalized.startswith(prefix + "{")
        for prefix in prefixes
    )


def http_snapshot(base_url: str, prefixes: Tuple[str, ...]) -> CounterSnapshot:
    root = fetch_metric(base_url, "http.server.requests")
    if not root:
        return CounterSnapshot()

    candidates = sorted(
        {
            uri
            for uri in uri_values(root)
            if is_workload_uri(uri, prefixes)
        }
    )
    total_count = 0.0
    total_time_ms = 0.0
    max_time_ms = 0.0
    matched: List[str] = []

    for uri in candidates:
        payload = fetch_metric(
            base_url,
            "http.server.requests",
            (("uri", uri),),
        )
        if not payload:
            continue

        count = measurement_value(payload, "COUNT")
        total_seconds = measurement_value(payload, "TOTAL_TIME")
        max_seconds = measurement_value(payload, "MAX")

        if count > 0:
            matched.append(uri)
        total_count += count
        total_time_ms += total_seconds * 1000.0
        max_time_ms = max(max_time_ms, max_seconds * 1000.0)

    return CounterSnapshot(
        count=total_count,
        total_time_ms=total_time_ms,
        max_time_ms=max_time_ms,
        matched_uris=tuple(matched),
        available=True,
    )


def scalar_metric(
    base_url: str,
    metric_name: str,
    statistic: str = "VALUE",
) -> Tuple[float, bool]:
    payload = fetch_metric(base_url, metric_name)
    if not payload:
        return 0.0, False
    return measurement_value(payload, statistic), True


def heap_percent(base_url: str) -> Tuple[float, bool]:
    used, used_available = scalar_metric(base_url, "jvm.memory.used")
    maximum, max_available = scalar_metric(base_url, "jvm.memory.max")
    if not (used_available and max_available and maximum > 0):
        return 0.0, False
    return round((used / maximum) * 100.0, 4), True


def get_before_snapshot(service_name: str) -> Optional[Dict[str, Any]]:
    query = (
        'SELECT LAST("request_counter") AS "request_counter", '
        'LAST("total_time_counter_ms") AS "total_time_counter_ms" '
        'FROM "aiperf_service_metrics" '
        f'WHERE "run_id"=\'{escape_literal(RUN_ID)}\' '
        f'AND "service_name"=\'{escape_literal(service_name)}\' '
        'AND "sample_phase"=\'before\''
    )
    points = list(CLIENT.query(query).get_points())
    return points[0] if points else None


def build_point(service_name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    base_url = str(spec["url"])
    snapshot = http_snapshot(base_url, tuple(spec["paths"]))

    system_cpu, system_cpu_available = scalar_metric(
        base_url,
        "system.cpu.usage",
    )
    process_cpu, process_cpu_available = scalar_metric(
        base_url,
        "process.cpu.usage",
    )
    threads, threads_available = scalar_metric(
        base_url,
        "jvm.threads.live",
    )
    heap_pct, heap_available = heap_percent(base_url)

    active, active_available = scalar_metric(
        base_url,
        "http.server.requests.active",
        "ACTIVE_TASKS",
    )
    if not active_available:
        active, active_available = scalar_metric(
            base_url,
            "http.server.requests.active",
            "VALUE",
        )

    executor, executor_available = scalar_metric(
        base_url,
        "executor.active",
        "ACTIVE_TASKS",
    )
    if not executor_available:
        executor, executor_available = scalar_metric(
            base_url,
            "executor.active",
            "VALUE",
        )

    request_count = 0.0
    average_response_ms = 0.0
    total_delta_ms = 0.0
    delta_available = False
    workload_status = "SNAPSHOT_ONLY"

    if PHASE == "after":
        before = get_before_snapshot(service_name)
        if before and snapshot.available:
            count_delta = snapshot.count - float(
                before.get("request_counter") or 0.0
            )
            time_delta = snapshot.total_time_ms - float(
                before.get("total_time_counter_ms") or 0.0
            )

            if count_delta >= 0 and time_delta >= 0:
                delta_available = True
                request_count = finite_nonnegative(count_delta)
                total_delta_ms = finite_nonnegative(time_delta)
                average_response_ms = (
                    round(total_delta_ms / request_count, 4)
                    if request_count > 0
                    else 0.0
                )
                workload_status = (
                    "VALID"
                    if request_count >= MIN_SERVICE_REQUESTS
                    else "INSUFFICIENT_TRAFFIC"
                )
            else:
                workload_status = "COUNTER_RESET"
        else:
            workload_status = "MISSING_BEFORE_SNAPSHOT"

    coverage_flags = (
        snapshot.available,
        system_cpu_available,
        process_cpu_available,
        threads_available,
        heap_available,
    )
    metric_coverage = sum(integer_flag(value) for value in coverage_flags) / len(
        coverage_flags
    )

    fields: Dict[str, Any] = {
        "request_counter": finite_nonnegative(snapshot.count),
        "total_time_counter_ms": finite_nonnegative(snapshot.total_time_ms),
        "counter_max_response_time_ms": finite_nonnegative(
            snapshot.max_time_ms
        ),
        "request_count": finite_nonnegative(request_count),
        "total_time_delta_ms": finite_nonnegative(total_delta_ms),
        "avg_response_time_ms": finite_nonnegative(average_response_ms),
        # InfluxDB OSS 1.8 field contract: integer, never bool.
        "request_count_available": integer_flag(delta_available),
        "workload_status": workload_status,
        "matched_uri_count": int(len(snapshot.matched_uris)),
        "matched_uris_json": json.dumps(snapshot.matched_uris),
        "system_cpu_usage": finite_nonnegative(system_cpu),
        "process_cpu_usage": finite_nonnegative(process_cpu),
        "jvm_memory_used_mb": 0.0,
        "jvm_threads_live": finite_nonnegative(threads),
        "active_requests": finite_nonnegative(active),
        "executor_active": finite_nonnegative(executor),
        "heap_pct": finite_nonnegative(heap_pct),
        "gc_overhead": 0.0,
        "metric_coverage": round(metric_coverage, 4),
    }

    LOG.info(
        "%s | phase=%s | counter=%.0f | run_delta=%.0f | "
        "avg_ms=%.2f | status=%s | uris=%s",
        service_name,
        PHASE,
        snapshot.count,
        request_count,
        average_response_ms,
        workload_status,
        snapshot.matched_uris,
    )

    return {
        "measurement": "aiperf_service_metrics",
        "tags": {
            "run_id": RUN_ID,
            "service_name": service_name,
            "sample_phase": PHASE,
        },
        "fields": fields,
    }


def validate_points(points: List[Dict[str, Any]]) -> None:
    if len(points) != len(SERVICES):
        raise RuntimeError(
            f"Expected {len(SERVICES)} service points, found {len(points)}"
        )

    for point in points:
        fields = point.get("fields") or {}
        flag = fields.get("request_count_available")
        if isinstance(flag, bool) or not isinstance(flag, int):
            service_name = (point.get("tags") or {}).get(
                "service_name",
                "UNKNOWN",
            )
            raise TypeError(
                "request_count_available must be an integer for "
                f"service {service_name}; received {type(flag).__name__}"
            )
        if flag not in {0, 1}:
            raise ValueError(
                "request_count_available must contain only 0 or 1"
            )


def main() -> int:
    LOG.info(
        "RUN_ID=%s phase=%s database=%s",
        RUN_ID,
        PHASE,
        INFLUX_DB,
    )

    points = [
        build_point(service_name, spec)
        for service_name, spec in SERVICES.items()
    ]
    validate_points(points)

    written = CLIENT.write_points(points, time_precision="ms")
    if written is False:
        raise RuntimeError("InfluxDB write_points returned False")

    if PHASE == "after":
        invalid_services = [
            point["tags"]["service_name"]
            for point in points
            if point["fields"]["workload_status"] != "VALID"
        ]
        if invalid_services:
            LOG.warning(
                "Workload telemetry is not valid for: %s",
                ", ".join(invalid_services),
            )
            fail_on_missing = os.getenv(
                "AIPERF_FAIL_ON_MISSING_SERVICE_TRAFFIC",
                "false",
            ).strip().lower() in {"1", "true", "yes"}
            if fail_on_missing:
                return 2

    LOG.info(
        "AIPERF SERVICE METRICS WRITTEN SUCCESSFULLY | RUN_ID=%s",
        RUN_ID,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        LOG.exception(
            "AIPERF SERVICE METRICS COLLECTION FAILED | RUN_ID=%s | error=%s",
            RUN_ID,
            exc,
        )
        raise SystemExit(1)
    finally:
        SESSION.close()
        CLIENT.close()

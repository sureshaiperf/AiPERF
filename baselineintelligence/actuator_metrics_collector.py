import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests
from influxdb import InfluxDBClient

LOG = logging.getLogger("aiperf.actuator")
logging.basicConfig(level=os.getenv("AIPERF_LOG_LEVEL", "INFO"), format="%(asctime)s | %(levelname)s | %(message)s")

INFLUX_HOST = os.getenv("INFLUX_HOST", "localhost")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DB", "jmeter")
INFLUX_USER = os.getenv("INFLUX_USER") or None
INFLUX_PASSWORD = os.getenv("INFLUX_PASSWORD") or None
HTTP_TIMEOUT = float(os.getenv("AIPERF_HTTP_TIMEOUT_SECONDS", "10"))
VERIFY_TLS = os.getenv("AIPERF_VERIFY_TLS", "true").lower() in {"1", "true", "yes"}
MIN_SERVICE_REQUESTS = int(os.getenv("AIPERF_MIN_SERVICE_REQUESTS", "1"))

RUN_ID = (os.getenv("RUN_ID") or "").strip()
PHASE = (os.getenv("AIPERF_SERVICE_PHASE") or "after").strip().lower()
if not RUN_ID:
    raise RuntimeError("RUN_ID environment variable is required")
if PHASE not in {"before", "after"}:
    raise ValueError("AIPERF_SERVICE_PHASE must be 'before' or 'after'")

SERVICES: Dict[str, Dict[str, Any]] = {
    "gateway": {"url": os.getenv("AIPERF_GATEWAY_URL", "http://localhost:8090"), "paths": ("/users", "/products", "/orders")},
    "user-service": {"url": os.getenv("AIPERF_USER_SERVICE_URL", "http://localhost:8081"), "paths": ("/users",)},
    "product-service": {"url": os.getenv("AIPERF_PRODUCT_SERVICE_URL", "http://localhost:8082"), "paths": ("/products",)},
    "order-service": {"url": os.getenv("AIPERF_ORDER_SERVICE_URL", "http://localhost:8083"), "paths": ("/orders",)},
}
EXCLUDED_URI_TOKENS = ("/actuator", "management", "prometheus")
EXCLUDED_URI_VALUES = {"UNKNOWN", "NOT_FOUND", "REDIRECTION", "root", "**"}

@dataclass
class CounterSnapshot:
    count: float = 0.0
    total_time_ms: float = 0.0
    max_time_ms: float = 0.0
    matched_uris: Tuple[str, ...] = ()
    available: bool = False

session = requests.Session()
session.headers.update({"Accept": "application/json", "User-Agent": "AiPERF-Actuator-Collector/2.0"})
client = InfluxDBClient(host=INFLUX_HOST, port=INFLUX_PORT, username=INFLUX_USER, password=INFLUX_PASSWORD, database=INFLUX_DB, timeout=15)


def _metric(base_url: str, name: str, tags: Optional[Iterable[Tuple[str, str]]] = None) -> Optional[Dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/actuator/metrics/{name}"
    if tags:
        query = "&".join(f"tag={quote(str(k), safe='')}:{quote(str(v), safe='')}" for k, v in tags)
        url = f"{url}?{query}"
    try:
        response = session.get(url, timeout=HTTP_TIMEOUT, verify=VERIFY_TLS)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (requests.RequestException, ValueError) as exc:
        LOG.warning("Metric unavailable: service=%s metric=%s error=%s", base_url, name, exc)
        return None


def _measurement(payload: Optional[Dict[str, Any]], statistic: str) -> float:
    for item in (payload or {}).get("measurements", []):
        if str(item.get("statistic", "")).upper() == statistic:
            try:
                return float(item.get("value") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _uri_values(payload: Optional[Dict[str, Any]]) -> List[str]:
    for tag in (payload or {}).get("availableTags", []):
        if tag.get("tag") == "uri":
            return [str(v) for v in tag.get("values", [])]
    return []


def _is_workload_uri(uri: str, prefixes: Tuple[str, ...]) -> bool:
    normalized = uri.strip()
    lowered = normalized.lower()
    if not normalized or normalized in EXCLUDED_URI_VALUES:
        return False
    if any(token in lowered for token in EXCLUDED_URI_TOKENS):
        return False
    return any(normalized == p or normalized.startswith(p + "/") or normalized.startswith(p + "{") for p in prefixes)


def http_snapshot(base_url: str, prefixes: Tuple[str, ...]) -> CounterSnapshot:
    root = _metric(base_url, "http.server.requests")
    if not root:
        return CounterSnapshot()
    candidates = sorted({u for u in _uri_values(root) if _is_workload_uri(u, prefixes)})
    total_count = total_time_ms = max_time_ms = 0.0
    matched: List[str] = []
    for uri in candidates:
        payload = _metric(base_url, "http.server.requests", (("uri", uri),))
        if not payload:
            continue
        count = _measurement(payload, "COUNT")
        total_seconds = _measurement(payload, "TOTAL_TIME")
        max_seconds = _measurement(payload, "MAX")
        if count > 0:
            matched.append(uri)
        total_count += count
        total_time_ms += total_seconds * 1000.0
        max_time_ms = max(max_time_ms, max_seconds * 1000.0)
    return CounterSnapshot(total_count, total_time_ms, max_time_ms, tuple(matched), True)


def scalar_metric(base_url: str, metric_name: str, statistic: str = "VALUE") -> Tuple[float, bool]:
    payload = _metric(base_url, metric_name)
    if not payload:
        return 0.0, False
    return _measurement(payload, statistic), True


def heap_percent(base_url: str) -> Tuple[float, bool]:
    used, used_ok = scalar_metric(base_url, "jvm.memory.used")
    maximum, max_ok = scalar_metric(base_url, "jvm.memory.max")
    if not (used_ok and max_ok and maximum > 0):
        return 0.0, False
    return round((used / maximum) * 100.0, 4), True


def escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def before_snapshot(service_name: str) -> Optional[Dict[str, Any]]:
    query = (
        'SELECT LAST("request_counter") AS "request_counter", '
        'LAST("total_time_counter_ms") AS "total_time_counter_ms" '
        'FROM "aiperf_service_metrics" '
        f"WHERE \"run_id\"='{escape_literal(RUN_ID)}' "
        f"AND \"service_name\"='{escape_literal(service_name)}' "
        "AND \"sample_phase\"='before'"
    )
    points = list(client.query(query).get_points())
    return points[0] if points else None


def finite_nonnegative(value: float) -> float:
    return round(max(0.0, float(value)), 4)


def build_point(service_name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    base_url = str(spec["url"])
    snapshot = http_snapshot(base_url, tuple(spec["paths"]))
    system_cpu, system_cpu_ok = scalar_metric(base_url, "system.cpu.usage")
    process_cpu, process_cpu_ok = scalar_metric(base_url, "process.cpu.usage")
    threads, threads_ok = scalar_metric(base_url, "jvm.threads.live")
    heap_pct, heap_ok = heap_percent(base_url)
    active, active_ok = scalar_metric(base_url, "http.server.requests.active", "ACTIVE_TASKS")
    if not active_ok:
        active, active_ok = scalar_metric(base_url, "http.server.requests.active", "VALUE")
    executor, executor_ok = scalar_metric(base_url, "executor.active", "ACTIVE_TASKS")
    if not executor_ok:
        executor, executor_ok = scalar_metric(base_url, "executor.active", "VALUE")

    request_count = avg_ms = total_delta_ms = 0.0
    delta_available = False
    workload_status = "SNAPSHOT_ONLY"
    if PHASE == "after":
        before = before_snapshot(service_name)
        if before and snapshot.available:
            count_delta = snapshot.count - float(before.get("request_counter") or 0.0)
            time_delta = snapshot.total_time_ms - float(before.get("total_time_counter_ms") or 0.0)
            if count_delta >= 0 and time_delta >= 0:
                delta_available = True
                request_count = finite_nonnegative(count_delta)
                total_delta_ms = finite_nonnegative(time_delta)
                avg_ms = round(total_delta_ms / request_count, 4) if request_count > 0 else 0.0
                workload_status = "VALID" if request_count >= MIN_SERVICE_REQUESTS else "INSUFFICIENT_TRAFFIC"
            else:
                workload_status = "COUNTER_RESET"
        else:
            workload_status = "MISSING_BEFORE_SNAPSHOT"

    metric_coverage = sum((snapshot.available, system_cpu_ok, process_cpu_ok, threads_ok, heap_ok)) / 5.0
    fields: Dict[str, Any] = {
        "request_counter": finite_nonnegative(snapshot.count),
        "total_time_counter_ms": finite_nonnegative(snapshot.total_time_ms),
        "counter_max_response_time_ms": finite_nonnegative(snapshot.max_time_ms),
        "request_count": request_count,
        "total_time_delta_ms": total_delta_ms,
        "avg_response_time_ms": avg_ms,
        "request_count_available": bool(delta_available),
        "workload_status": workload_status,
        "matched_uri_count": len(snapshot.matched_uris),
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
    LOG.info("%s | phase=%s | counter=%.0f | run_delta=%.0f | avg_ms=%.2f | status=%s | uris=%s", service_name, PHASE, snapshot.count, request_count, avg_ms, workload_status, snapshot.matched_uris)
    return {"measurement": "aiperf_service_metrics", "tags": {"run_id": RUN_ID, "service_name": service_name, "sample_phase": PHASE}, "fields": fields}


def main() -> int:
    LOG.info("RUN_ID=%s phase=%s database=%s", RUN_ID, PHASE, INFLUX_DB)
    points = [build_point(name, spec) for name, spec in SERVICES.items()]
    if not client.write_points(points, time_precision="ms"):
        raise RuntimeError("InfluxDB write_points returned False")
    if PHASE == "after":
        bad = [p["tags"]["service_name"] for p in points if p["fields"]["workload_status"] != "VALID"]
        if bad:
            LOG.warning("Workload telemetry is not valid for: %s", ", ".join(bad))
            if os.getenv("AIPERF_FAIL_ON_MISSING_SERVICE_TRAFFIC", "false").lower() in {"1", "true", "yes"}:
                return 2
    LOG.info("AIPERF SERVICE METRICS WRITTEN SUCCESSFULLY | RUN_ID=%s", RUN_ID)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        session.close()
        client.close()

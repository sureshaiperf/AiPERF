import os
import requests
from influxdb import InfluxDBClient

# =====================================================
# InfluxDB Configuration
# =====================================================

INFLUX_HOST = "localhost"
INFLUX_PORT = 8086
INFLUX_DB = "jmeter"

client = InfluxDBClient(
    host=INFLUX_HOST,
    port=INFLUX_PORT
)

client.switch_database(INFLUX_DB)

# =====================================================
# RUN ID
# =====================================================

run_id = os.getenv("RUN_ID")
phase = os.getenv("AIPERF_SERVICE_PHASE", "after").lower()

if not run_id:
    raise Exception("RUN_ID environment variable not found")
if phase not in {"before", "after"}:
    raise ValueError("AIPERF_SERVICE_PHASE must be 'before' or 'after'")

print(f"Using RUN_ID = {run_id}")
print(f"Service metric phase = {phase}")

# =====================================================
# SERVICES
# =====================================================

SERVICES = {
    "gateway": "http://localhost:8090",
    "user-service": "http://localhost:8081",
    "product-service": "http://localhost:8082",
    "order-service": "http://localhost:8083"
}

# =====================================================
# GENERIC METRIC READER
# =====================================================

def get_metric(base_url, metric_name):

    try:

        response = requests.get(
            f"{base_url}/actuator/metrics/{metric_name}",
            timeout=10
        )

        if response.status_code != 200:
            return 0

        payload = response.json()

        measurements = payload.get(
            "measurements", []
        )

        if not measurements:
            return 0

        return measurements[0].get(
            "value",
            0
        )

    except Exception as ex:

        print(
            f"ERROR reading {metric_name}: {ex}"
        )

        return 0

# =====================================================
# HTTP REQUEST METRICS
# =====================================================

def get_http_request_metrics(base_url):

    try:

        response = requests.get(
            f"{base_url}/actuator/metrics/http.server.requests",
            timeout=10
        )

        if response.status_code != 200:
            return {
                "count": 0,
                "avg_ms": 0,
                "max_ms": 0,
                "total_time_ms": 0,
                "available": False
            }

        payload = response.json()

        measurements = payload.get(
            "measurements",
            []
        )

        count = 0
        total_time = 0
        max_time = 0

        for item in measurements:

            stat = item.get("statistic")

            if stat == "COUNT":
                count = item.get("value", 0)

            elif stat == "TOTAL_TIME":
                total_time = item.get("value", 0)

            elif stat == "MAX":
                max_time = item.get("value", 0)

        avg_ms = 0

        if count > 0:
            avg_ms = (
                total_time / count
            ) * 1000

        return {
            "count": round(count, 2),
            "avg_ms": round(avg_ms, 2),
            "max_ms": round(max_time * 1000, 2),
            "total_time_ms": round(total_time * 1000, 2),
            "available": True
        }

    except Exception as ex:

        print(
            f"ERROR reading http.server.requests: {ex}"
        )

        return {
            "count": 0,
            "avg_ms": 0,
            "max_ms": 0,
            "total_time_ms": 0,
            "available": False
        }

# =====================================================
# HEAP UTILIZATION %
# =====================================================

def get_heap_percent(base_url):

    try:

        used = get_metric(
            base_url,
            "jvm.memory.used"
        )

        max_mem = get_metric(
            base_url,
            "jvm.memory.max"
        )

        if max_mem <= 0:
            return 0

        return round(
            (used / max_mem) * 100,
            2
        )

    except Exception as ex:

        print(
            f"ERROR calculating heap %: {ex}"
        )

        return 0

# =====================================================
# COLLECT METRICS
# =====================================================

json_body = []

for service_name, service_url in SERVICES.items():

    print("\n================================================")
    print(f"Collecting Metrics : {service_name}")
    print("================================================")

    http_metrics = get_http_request_metrics(
        service_url
    )

    request_count = 0.0
    avg_response_time_ms = 0.0
    request_count_available = False
    if phase == "after":
        before_result = client.query(
            f"""
            SELECT LAST(request_counter) AS request_counter,
                   LAST(total_time_counter_ms) AS total_time_counter_ms
            FROM aiperf_service_metrics
            WHERE run_id='{run_id}'
            AND service_name='{service_name}'
            AND sample_phase='before'
            """
        )
        before_points = list(before_result.get_points())
        before_count = float(
            before_points[0].get("request_counter") or 0
        ) if before_points else 0.0
        before_total_time_ms = float(
            before_points[0].get("total_time_counter_ms") or 0
        ) if before_points else 0.0
        count_delta = http_metrics["count"] - before_count
        total_time_delta_ms = (
            http_metrics["total_time_ms"] - before_total_time_ms
        )
        request_count_available = (
            http_metrics["available"]
            and bool(before_points)
            and count_delta >= 0
            and total_time_delta_ms >= 0
        )
        if request_count_available:
            request_count = round(count_delta, 2)
            if request_count > 0:
                avg_response_time_ms = round(
                    total_time_delta_ms / request_count, 2
                )

    max_response_time_ms = 0.0

    active_requests = get_metric(
        service_url,
        "http.server.requests.active"
    )

    executor_active = get_metric(
        service_url,
        "executor.active"
    )

    gc_overhead = get_metric(
        service_url,
        "jvm.gc.overhead"
    )

    heap_pct = get_heap_percent(
        service_url
    )

    print(
        f"Request Count       : {request_count}"
    )

    print(
        f"Avg Response MS     : {avg_response_time_ms}"
    )

    print(
        "Max Response MS     : N/A (cumulative actuator maximum)"
    )

    print(
        f"Active Requests     : {active_requests}"
    )

    print(
        f"Executor Active     : {executor_active}"
    )

    print(
        f"Heap %              : {heap_pct}"
    )

    print(
        f"GC Overhead         : {gc_overhead}"
    )

    json_body.append({

        "measurement": "aiperf_service_metrics",

        "tags": {
            "run_id": run_id,
            "service_name": service_name,
            "sample_phase": phase
        },

        "fields": {

            "request_count":
                float(request_count),
            "request_count_available":
                int(request_count_available),
            "request_counter":
                float(http_metrics["count"]),
            "total_time_counter_ms":
                float(http_metrics["total_time_ms"]),

            "avg_response_time_ms":
                float(avg_response_time_ms),

            "max_response_time_ms":
                float(max_response_time_ms),

            "active_requests":
                float(active_requests),

            "executor_active":
                float(executor_active),

            "heap_pct":
                float(heap_pct),

            "gc_overhead":
                float(gc_overhead)
        }
    })

# =====================================================
# WRITE TO INFLUXDB
# =====================================================

if json_body:

    client.write_points(json_body)

    print("\n==========================================")
    print("AIPERF SERVICE METRICS WRITTEN SUCCESSFULLY")
    print(f"RUN_ID : {run_id}")
    print("==========================================")

else:

    print("No metrics collected.")
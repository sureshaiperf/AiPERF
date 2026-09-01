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

if not run_id:
    raise Exception("RUN_ID environment variable not found")

print(f"Using RUN_ID = {run_id}")

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
                "max_ms": 0
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
            "max_ms": round(max_time * 1000, 2)
        }

    except Exception as ex:

        print(
            f"ERROR reading http.server.requests: {ex}"
        )

        return {
            "count": 0,
            "avg_ms": 0,
            "max_ms": 0
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

    request_count = http_metrics["count"]

    avg_response_time_ms = (
        http_metrics["avg_ms"]
    )

    max_response_time_ms = (
        http_metrics["max_ms"]
    )

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
        f"Max Response MS     : {max_response_time_ms}"
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
            "service_name": service_name
        },

        "fields": {

            "request_count":
                float(request_count),

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
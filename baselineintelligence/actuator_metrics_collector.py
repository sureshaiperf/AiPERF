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
# Run ID
# =====================================================

run_id = os.getenv("RUN_ID")

if not run_id:
    raise Exception("RUN_ID environment variable not found")

print(f"Using RUN_ID = {run_id}")

# =====================================================
# Services
# =====================================================

SERVICES = {
    "gateway": "http://localhost:8090",
    "user-service": "http://localhost:8081",
    "product-service": "http://localhost:8082",
    "order-service": "http://localhost:8083"
}

# =====================================================
# Generic Metric Reader
# =====================================================

def get_metric(base_url, metric_name):

    try:

        url = f"{base_url}/actuator/metrics/{metric_name}"

        response = requests.get(
            url,
            timeout=10
        )

        if response.status_code != 200:
            return None

        payload = response.json()

        measurements = payload.get(
            "measurements",
            []
        )

        if not measurements:
            return None

        return measurements[0]["value"]

    except Exception as ex:

        print(
            f"ERROR reading {metric_name} : {ex}"
        )

        return None


# =====================================================
# Collect Metrics
# =====================================================

json_body = []

for service_name, service_url in SERVICES.items():

    print(f"\nCollecting metrics from {service_name}")

    # -------------------------------------------------
    # System Metrics
    # -------------------------------------------------

    system_cpu_raw = get_metric(
        service_url,
        "system.cpu.usage"
    )

    process_cpu_raw = get_metric(
        service_url,
        "process.cpu.usage"
    )

    system_cpu = round(
        (system_cpu_raw or 0) * 100,
        2
    )

    process_cpu = round(
        (process_cpu_raw or 0) * 100,
        2
    )

    # -------------------------------------------------
    # JVM Metrics
    # -------------------------------------------------

    jvm_memory_used_bytes = get_metric(
        service_url,
        "jvm.memory.used"
    )

    jvm_memory_max_bytes = get_metric(
        service_url,
        "jvm.memory.max"
    )

    jvm_threads_live = get_metric(
        service_url,
        "jvm.threads.live"
    )

    jvm_memory_used_mb = round(
        (jvm_memory_used_bytes or 0) / 1024 / 1024,
        2
    )

    jvm_memory_max_mb = round(
        (jvm_memory_max_bytes or 0) / 1024 / 1024,
        2
    )

    # -------------------------------------------------
    # Application Metrics
    # -------------------------------------------------

    http_server_requests = get_metric(
        service_url,
        "http.server.requests"
    )

    # -------------------------------------------------
    # Logging
    # -------------------------------------------------

    print(f"System CPU %         : {system_cpu}")
    print(f"Process CPU %        : {process_cpu}")
    print(f"JVM Memory Used MB   : {jvm_memory_used_mb}")
    print(f"JVM Memory Max MB    : {jvm_memory_max_mb}")
    print(f"JVM Threads Live     : {jvm_threads_live}")
    print(f"HTTP Requests        : {http_server_requests}")

    # -------------------------------------------------
    # Write Point
    # -------------------------------------------------

    json_body.append({
        "measurement": "aiperf_service_metrics",

        "tags": {
            "run_id": run_id,
            "service_name": service_name
        },

        "fields": {

            # System

            "system_cpu_usage":
                float(system_cpu),

            "process_cpu_usage":
                float(process_cpu),

            # JVM

            "jvm_memory_used_mb":
                float(jvm_memory_used_mb),

            "jvm_memory_max_mb":
                float(jvm_memory_max_mb),

            "jvm_threads_live":
                int(jvm_threads_live or 0),

            # Application

            "http_server_requests":
                float(http_server_requests or 0)
        }
    })

# =====================================================
# Persist
# =====================================================

if json_body:

    client.write_points(json_body)

    print("\n===================================")
    print("Metrics Successfully Written")
    print(f"RUN_ID : {run_id}")
    print("===================================")

else:

    print("No metrics collected")
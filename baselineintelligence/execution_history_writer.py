#Execution history writer

import os
from influxdb import InfluxDBClient
from datetime import datetime

# --------------------------------------------------
# Jenkins Variables
# --------------------------------------------------

build_number = os.getenv("BUILD_NUMBER", "0")
job_name = os.getenv("JOB_NAME", "MANUAL")

# --------------------------------------------------
# Execution Metadata
# --------------------------------------------------

application = "AiPERF"
environment = "QA"
test_name = "API_Test"

# --------------------------------------------------
# InfluxDB Connection
# --------------------------------------------------

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

# --------------------------------------------------
# Read Service Metrics
# --------------------------------------------------

def get_service_metrics(run_id, service_name):

    query = f"""
    SELECT
        LAST(system_cpu_usage) as system_cpu_usage,
        LAST(process_cpu_usage) as process_cpu_usage,
        LAST(jvm_memory_used_mb) as jvm_memory_used_mb,
        LAST(jvm_memory_max_mb) as jvm_memory_max_mb,
        LAST(jvm_threads_live) as jvm_threads_live,
        LAST(http_server_requests) as http_server_requests
    FROM aiperf_service_metrics
    WHERE run_id='{run_id}'
    AND service_name='{service_name}'
    """

    result = client.query(query)

    points = list(result.get_points())

    if points:
        return {
            "system_cpu": points[0].get("system_cpu_usage", 0),
            "process_cpu": points[0].get("process_cpu_usage", 0),
            "jvm_memory": points[0].get("jvm_memory_used_mb", 0),
            "jvm_memory_max": points[0].get("jvm_memory_max_mb", 0),
            "jvm_threads": points[0].get("jvm_threads_live", 0),
            "http_requests": points[0].get("http_server_requests", 0)
        }

    return {
        "system_cpu": 0,
        "process_cpu": 0,
        "jvm_memory": 0,
        "jvm_memory_max": 0,
        "jvm_threads": 0,
        "http_requests": 0
    }


# --------------------------------------------------
# Execution Summary
# (Later read from JMeter/JTL)
# --------------------------------------------------

from datetime import datetime

#run_id = f"RUN_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
run_id = os.getenv("RUN_ID")
print(f"Using RUN_ID = {run_id}")

if not run_id:
    raise Exception("RUN_ID environment variable not found")

#print(f"Using Run ID: {run_id}")

result = client.query(
    """
    SELECT *
    FROM jmeter
    WHERE transaction='all'
    ORDER BY time DESC
    LIMIT 1
    """
)

points = list(result.get_points())

if not points:
    raise Exception("No JMeter execution data found")

latest = points[0]

avg_rt = latest.get("avg", 0)
p90 = latest.get("pct90.0", 0)
p95 = latest.get("pct95.0", 0)
p99 = latest.get("pct99.0", 0)
errors = latest.get("countError", 0)
throughput = latest.get("count", 0)

# Calculate Error Rate %

if throughput > 0:
    error_rate = (errors / throughput) * 100
else:
    error_rate = 0
print(f"Error Rate : {error_rate:.2f}%")

#Read service metrics
gateway = get_service_metrics(run_id, "gateway")
user = get_service_metrics(run_id, "user-service")
product = get_service_metrics(run_id, "product-service")
order = get_service_metrics(run_id, "order-service")

print("Latest Metrics Retrieved")
print(f"Avg RT   : {avg_rt}")
print(f"P90      : {p90}")
print(f"P95      : {p95}")
print(f"P99      : {p99}")
print(f"Errors   : {errors}")
#print(f"Requests : {requests}")

# --------------------------------------------------
# Execution Status - have to modify further in next phase
# --------------------------------------------------

if errors > 0:
    execution_status = "FAIL"
else:
    execution_status = "PASS"


# --------------------------------------------------
# Write to InfluxDB
# --------------------------------------------------

json_body = [{
    "measurement": "aiperf_execution_history",

    "tags": {
        "application": application,
        "environment": environment,
        "test_name": test_name,
        "job_name": job_name
    },

    
    "fields": {
        "run_id": run_id,
        "build_number": int(build_number) if str(build_number).isdigit() else 0,
        "avg_rt": float(avg_rt),
        "p90": float(p90),
        "p95": float(p95),
        "p99": float(p99),
        "errors": int(errors),
        #"requests": int(requests),
        "throughput": float(throughput),
        "execution_status": execution_status
    }
}]

client.write_points(json_body)

print("Execution History Written Successfully")
print("Run ID:", run_id)

# --------------------------------------------------
# Save Fingerprint
# --------------------------------------------------

fingerprint_body = [{
    "measurement": "aiperf_execution_fingerprint",

    "tags": {
        "run_id": run_id,
        "application": application,
        "environment": environment,
        "test_name": test_name
    },

    "fields": {

        # ==========================================
        # Performance Metrics
        # ==========================================

        "avg_rt": float(avg_rt),
        "p95": float(p95),
        "p99": float(p99),
        "throughput": float(throughput),
        "error_rate": float(error_rate),

        # ==========================================
        # Host Metrics
        # ==========================================

        "host_system_cpu": float(gateway["system_cpu"]),

        # ==========================================
        # Gateway Metrics
        # ==========================================

        "gateway_process_cpu": float(gateway["process_cpu"]),
        "gateway_jvm_memory": float(gateway["jvm_memory"]),
        "gateway_jvm_threads": float(gateway["jvm_threads"]),
        "gateway_http_requests": float(gateway["http_requests"]),

        # ==========================================
        # User Metrics
        # ==========================================

        "user_process_cpu": float(user["process_cpu"]),
        "user_jvm_memory": float(user["jvm_memory"]),
        "user_jvm_threads": float(user["jvm_threads"]),
        "user_http_requests": float(user["http_requests"]),

        # ==========================================
        # Product Metrics
        # ==========================================

        "product_process_cpu": float(product["process_cpu"]),
        "product_jvm_memory": float(product["jvm_memory"]),
        "product_jvm_threads": float(product["jvm_threads"]),
        "product_http_requests": float(product["http_requests"]),

        # ==========================================
        # Order Metrics
        # ==========================================

        "order_process_cpu": float(order["process_cpu"]),
        "order_jvm_memory": float(order["jvm_memory"]),
        "order_jvm_threads": float(order["jvm_threads"]),
        "order_http_requests": float(order["http_requests"])
    }
}]

client.write_points(fingerprint_body)

print("=================================")
print("Execution Fingerprint Written")
print(f"Run ID : {run_id}")
print("=================================")
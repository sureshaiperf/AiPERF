#Execution history writer

import os
from influxdb import InfluxDBClient
from jmeter_run_metrics import read_jtl

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
    LAST(heap_pct) as heap_pct,
    LAST(active_requests) as active_requests,
    LAST(executor_active) as executor_active,
    LAST(avg_response_time_ms) as avg_response_time_ms,
    LAST(request_count) as request_count,
    LAST(request_count_available) as request_count_available
    FROM aiperf_service_metrics
    WHERE run_id='{run_id}'
    AND service_name='{service_name}'
    AND sample_phase='after'
    """

    result = client.query(query)

    points = list(result.get_points())

    if points:
        return {
            "heap_pct": points[0].get("heap_pct", 0),
            "active_requests": points[0].get("active_requests", 0),
            "executor_active": points[0].get("executor_active", 0),
            "avg_response_time_ms": points[0].get("avg_response_time_ms", 0),
            "request_count": points[0].get("request_count", 0),
            "request_count_available": points[0].get(
                "request_count_available", 0
            ),
        }

    return {
        "heap_pct": 0,
        "active_requests": 0,
        "executor_active": 0,
        "avg_response_time_ms": 0,
        "request_count": 0,
        "request_count_available": 0,
    }


# --------------------------------------------------
# Execution Summary
# (Later read from JMeter/JTL)
# --------------------------------------------------

from datetime import datetime

#run_id = f"RUN_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
run_id = os.getenv("RUN_ID")
run_start_epoch = os.getenv("RUN_START_EPOCH")
run_end_epoch = os.getenv("RUN_END_EPOCH")
print(f"Using RUN_ID = {run_id}")

if not run_id or not run_start_epoch or not run_end_epoch:
    raise RuntimeError(
        "RUN_ID, RUN_START_EPOCH, and RUN_END_EPOCH are required"
    )

#print(f"Using Run ID: {run_id}")

metrics = {}
jtl_path = os.getenv("JTL_PATH")
if jtl_path and os.path.exists(jtl_path):
    metrics = read_jtl(jtl_path)

if metrics.get("all"):
    latest = metrics["all"]
else:
    result = client.query(
        f"""
    SELECT *
    FROM jmeter
    WHERE time >= {int(run_start_epoch)}ms
    AND time <= {int(run_end_epoch)}ms
    AND transaction='all'
    AND statut='all'
    ORDER BY time DESC
    LIMIT 1
    """
    )
    points = list(result.get_points())
    if not points:
        raise Exception("No JMeter execution data found")
    point = points[0]
    latest = {
        "avg_rt": point.get("avg", 0),
        "p90": point.get("pct90.0", 0),
        "p95": point.get("pct95.0", 0),
        "p99": point.get("pct99.0", 0),
        "errors": point.get("countError", 0),
        "samples": point.get("count", 0),
    }

avg_rt = latest.get("avg_rt", 0)
p90 = latest.get("p90", 0)
p95 = latest.get("p95", 0)
p99 = latest.get("p99", 0)
errors = latest.get("errors", 0)
throughput = latest.get("samples", 0)

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
        "run_start_epoch": int(run_start_epoch),
        "run_end_epoch": int(run_end_epoch),
        "duration_seconds": round(
            (int(run_end_epoch) - int(run_start_epoch)) / 1000,
            3,
        ),
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

        "gateway_heap_pct": float(gateway["heap_pct"]),
        "gateway_active_requests": float(gateway["active_requests"]),
        "gateway_executor_active": float(gateway["executor_active"]),
        "gateway_avg_response_time_ms": float(gateway["avg_response_time_ms"]),
        "gateway_request_count": float(gateway["request_count"]),
        "gateway_request_count_available": int(
            gateway["request_count_available"]
        ),
        "user_heap_pct": float(user["heap_pct"]),
        "user_active_requests": float(user["active_requests"]),
        "user_executor_active": float(user["executor_active"]),
        "user_avg_response_time_ms": float(user["avg_response_time_ms"]),
        "user_request_count": float(user["request_count"]),
        "user_request_count_available": int(
            user["request_count_available"]
        ),
        "product_heap_pct": float(product["heap_pct"]),
        "product_active_requests": float(product["active_requests"]),
        "product_executor_active": float(product["executor_active"]),
        "product_avg_response_time_ms": float(product["avg_response_time_ms"]),
        "product_request_count": float(product["request_count"]),
        "product_request_count_available": int(
            product["request_count_available"]
        ),
        "order_heap_pct": float(order["heap_pct"]),
        "order_active_requests": float(order["active_requests"]),
        "order_executor_active": float(order["executor_active"]),
        "order_avg_response_time_ms": float(order["avg_response_time_ms"]),
        "order_request_count": float(order["request_count"]),
        "order_request_count_available": int(
            order["request_count_available"]
        )
    }
}]

client.write_points(fingerprint_body)

print("=================================")
print("Execution Fingerprint Written")
print(f"Run ID : {run_id}")
print("=================================")
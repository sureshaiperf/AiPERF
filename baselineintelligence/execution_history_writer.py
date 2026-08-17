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
# Execution Summary
# (Later read from JMeter/JTL)
# --------------------------------------------------

from datetime import datetime

run_id = f"RUN_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
# Save run_id for other AiPERF modules

with open("current_run_id.txt", "w") as f:
    f.write(run_id)

print(f"Run ID saved: {run_id}")

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
requests = latest.get("count", 0)

print("Latest Metrics Retrieved")
print(f"Avg RT   : {avg_rt}")
print(f"P90      : {p90}")
print(f"P95      : {p95}")
print(f"P99      : {p99}")
print(f"Errors   : {errors}")
print(f"Requests : {requests}")

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
        "requests": int(requests),
        "execution_status": execution_status
    }
}]

client.write_points(json_body)

print("Execution History Written Successfully")
print("Run ID:", run_id)
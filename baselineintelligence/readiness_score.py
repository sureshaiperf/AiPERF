from influxdb import InfluxDBClient
import os
import sys

# --------------------------------------------------
# InfluxDB Connection
# --------------------------------------------------

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

# --------------------------------------------------
# Read Baseline Analysis Results
# --------------------------------------------------

run_id = sys.argv[1] if len(sys.argv) > 1 else os.getenv("RUN_ID")
if not run_id:
    raise ValueError("RUN_ID must be provided as the first CLI argument or environment variable")

query = f"""
SELECT LAST("deviation")
FROM "aiperf_analysis"
WHERE "run_id"='{run_id}'
GROUP BY "transaction","status"
"""

result = client.query(query)

# --------------------------------------------------
# Readiness Score Calculation
# --------------------------------------------------

score = 100
fail_count = 0
warning_count = 0

print("\n===== AiPERF Release Readiness =====\n")

for measurement, points in result.items():

    tags = measurement[1]

    transaction = tags.get("transaction", "Unknown")
    status = tags.get("status", "PASS")

    if status == "FAIL":
        fail_count += 1
        score -= 25

    elif status == "WARNING":
        warning_count += 1
        score -= 10

    print(f"{transaction} : {status}")

# Prevent negative score
score = max(score, 0)

# --------------------------------------------------
# Determine Release Status
# --------------------------------------------------

if score >= 90:
    readiness = "PRODUCTION READY"

elif score >= 75:
    readiness = "READY WITH OBSERVATIONS"

elif score >= 60:
    readiness = "HIGH RISK"

else:
    readiness = "NOT READY"

print("\n--------------------------------")
print(f"Release Score : {score}/100")
print(f"Status        : {readiness}")

# --------------------------------------------------
# Store Result
# --------------------------------------------------

json_body = [
    {
        "measurement": "aiperf_release_readiness",
        "tags": {
            "application": "AiPERF",
            "run_id": run_id
        },
        "fields": {
            "release_score": int(score),
            "fail_count": int(fail_count),
            "warning_count": int(warning_count),
            "status": readiness
        }
    }
]

client.write_points(json_body)

print("\nStored in InfluxDB")
print(f"Run ID : {run_id}")
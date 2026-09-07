from influxdb import InfluxDBClient
import os
import sys
import argparse

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

parser = argparse.ArgumentParser()
parser.add_argument("--run-id", default=os.getenv("RUN_ID"))
args = parser.parse_args()
run_id = args.run_id
if not run_id:
    raise ValueError("RUN_ID must be provided with --run-id or the RUN_ID environment variable")

query = f"""
SELECT LAST("deviation")
FROM "aiperf_analysis"
WHERE "run_id"='{run_id}'
GROUP BY "transaction","status"
"""

result = client.query(query)

variance_result = client.query(
    f"""
    SELECT "variance_pct"
    FROM "aiperf_variance_ranking"
    WHERE "run_id"='{run_id}' AND "variance_pct">=15
    """
)

# --------------------------------------------------
# Readiness Score Calculation
# --------------------------------------------------

score = 100
fail_count = 0
warning_count = 0
variance_rows = list(variance_result.get_points())

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

for row in variance_rows:
    variance = float(row.get("variance_pct", 0))
    if variance >= 30:
        fail_count += 1
        score -= 25
    elif variance >= 15:
        warning_count += 1
        score -= 10

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
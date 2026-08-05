from influxdb import InfluxDBClient
from datetime import datetime

# Connect to InfluxDB
client = InfluxDBClient(
    host='localhost',
    port=8086,
    database='jmeter'
)

# Read latest baseline analysis results
query = """
SELECT LAST("deviation")
FROM "aiperf_analysis"
GROUP BY "transaction","status"
"""

result = client.query(query)

# Initialize scoring
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
if score < 0:
    score = 0

# Determine release status
if score >= 90:
    readiness = "PRODUCTION READY"

elif score >= 75:
    readiness = "READY WITH OBSERVATIONS"

elif score >= 60:
    readiness = "HIGH RISK"

else:
    readiness = "NOT READY"

print("\n----------------------------")
print(f"Release Score : {score}/100")
print(f"Status        : {readiness}")

# Generate unique run id
run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

# Store result in InfluxDB
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

print(f"\nStored in InfluxDB")
print(f"Run ID : {run_id}")
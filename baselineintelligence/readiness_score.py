from influxdb import InfluxDBClient
from datetime import datetime
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

# Optionally use BUILD_ID or VERSION environment variables to identify the build/version
# This allows the script to enforce a single readiness record per build/version/run.
build_id = os.getenv("BUILD_ID") or os.getenv("VERSION") or None

# Use build_id for deduplication when available, otherwise fall back to run_id
if build_id:
    identifier_key = "build_id"
    identifier_value = build_id
else:
    identifier_key = "run_id"
    identifier_value = run_id

# Prepare tags (include both for traceability)
tags = {
    "application": "AiPERF",
    "run_id": run_id
}
if build_id:
    tags["build_id"] = build_id

# Store result in InfluxDB
json_body = [
    {
        "measurement": "aiperf_release_readiness",
        "tags": tags,
        "fields": {
            "release_score": int(score),
            "fail_count": int(fail_count),
            "warning_count": int(warning_count),
            "status": readiness
        }
    }
]

# Ensure only one record exists per identifier (build_id OR run_id): delete existing records with the same identifier before writing
try:
    query = f"SELECT COUNT(release_score) as count FROM \"aiperf_release_readiness\" WHERE \"{identifier_key}\" = '{identifier_value}'"
    existing = client.query(query)
    count = 0
    for measurement, points in existing.items():
        for row in points:
            for v in row.values():
                try:
                    count = int(v)
                    break
                except Exception:
                    continue
    if count and count > 0:
        logger.info(f"Found {count} existing readiness record(s) for {identifier_key}={identifier_value}, deleting before write")
        client.query(f"DELETE FROM \"aiperf_release_readiness\" WHERE \"{identifier_key}\" = '{identifier_value}'")
except Exception as e:
    logger.warning(f"Could not check/delete existing readiness records for {identifier_key}={identifier_value}: {e}")

try:
    client.write_points(json_body)
    logger.info("Stored release readiness in InfluxDB")
    print(f"\nStored in InfluxDB")
    print(f"Run ID : {run_id}")
    if build_id:
        print(f"Build ID : {build_id}")
except Exception as e:
    logger.error(f"Failed to write readiness to InfluxDB: {e}")
    print(f"\nFailed to store readiness: {e}")
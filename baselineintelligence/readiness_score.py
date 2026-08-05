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

# CLI: accept optional --build-id to allow explicit CI-provided identifier
import argparse
parser = argparse.ArgumentParser(description="Compute and store release readiness")
parser.add_argument("--build-id", dest="build_id", help="Build identifier (overrides BUILD_ID env var)")
args = parser.parse_args()

# Gather build identifiers from CLI or environment (Jenkins provides BUILD_ID, BUILD_NUMBER, JOB_NAME)
build_id = args.build_id or os.getenv("BUILD_ID") or os.getenv("VERSION") or None
build_number = os.getenv("BUILD_NUMBER")
job_name = os.getenv("JOB_NAME")

# Use build_id for deduplication when available; otherwise try JOB_NAME+BUILD_NUMBER; fallback to run_id
if build_id:
    dedupe_mode = "build_id"
    identifier_expr = ("build_id", build_id)
elif job_name and build_number:
    dedupe_mode = "job_build"
    identifier_expr = ("job_name_build", f"{job_name}:{build_number}")
else:
    dedupe_mode = "run_id"
    identifier_expr = ("run_id", run_id)

# Prepare tags (include available CI info for traceability)
tags = {
    "application": "AiPERF",
    "run_id": run_id
}
if build_id:
    tags["build_id"] = build_id
if build_number:
    tags["build_number"] = build_number
if job_name:
    tags["job_name"] = job_name

# Store result body
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

# Build deduplication WHERE clause depending on mode
def dedupe_where_clause():
    mode, val = identifier_expr
    if mode == "build_id":
        return f'"build_id" = \'{val}\''
    elif mode == "job_build":
        # split composite
        job, num = val.split(":", 1)
        return f'"job_name" = \'{job}\' AND "build_number" = \'{num}\''
    else:
        return f'"run_id" = \'{val}\''

where_clause = dedupe_where_clause()

# Ensure only one record exists per identifier: delete existing records with the same identifier before writing
try:
    count_query = f"SELECT COUNT(release_score) as count FROM \"aiperf_release_readiness\" WHERE {where_clause}"
    existing = client.query(count_query)
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
        logger.info(f"Found {count} existing readiness record(s) for {identifier_expr}, deleting before write")
        client.query(f"DELETE FROM \"aiperf_release_readiness\" WHERE {where_clause}")
except Exception as e:
    logger.warning(f"Could not check/delete existing readiness records for {identifier_expr}: {e}")

# Write the new readiness record
try:
    client.write_points(json_body)
    logger.info("Stored release readiness in InfluxDB")
    print(f"\nStored in InfluxDB")
    print(f"Run ID : {run_id}")
    if build_id:
        print(f"Build ID : {build_id}")
    if job_name and build_number:
        print(f"Jenkins Job : {job_name}#{build_number}")
    print(f"Deduplication mode: {dedupe_mode}")
except Exception as e:
    logger.error(f"Failed to write readiness to InfluxDB: {e}")
    print(f"\nFailed to store readiness: {e}")
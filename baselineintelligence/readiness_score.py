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

def run_readiness(client=None, args=None, env=None):
    """Compute and store release readiness. Allows injecting a mock InfluxDB client and args for testing.
    If client is None, a real InfluxDBClient is created.
    If args is None, argparse will parse sys.argv.
    If env is None, os.environ is used."""
    # Resolve defaults
    if env is None:
        env = os.environ

    # Use the provided client or create a default one
    local_client = client if client is not None else InfluxDBClient(host='localhost', port=8086, database='jmeter')

    # Read latest baseline analysis results
    query = """
SELECT LAST("deviation")
FROM "aiperf_analysis"
GROUP BY "transaction","status"
"""

    try:
        result = local_client.query(query)
    except Exception as e:
        logger.warning(f"Failed to query aiperf_analysis: {e}")
        result = {}

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
    if args is None:
        parser = argparse.ArgumentParser(description="Compute and store release readiness")
        parser.add_argument("--build-id", dest="build_id", help="Build identifier (overrides BUILD_ID env var)")
        parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="Do not delete or write; only print what would be done")
        parser.add_argument("--jenkins-snippet", dest="jenkins_snippet", action="store_true", help="Print a recommended Jenkins pipeline snippet for invoking this script")
        args = parser.parse_args()

    # Gather build identifiers from CLI or environment (Jenkins provides BUILD_ID, BUILD_NUMBER, JOB_NAME)
    build_id = args.build_id or env.get("BUILD_ID") or env.get("VERSION") or None
    build_number = env.get("BUILD_NUMBER")
    job_name = env.get("JOB_NAME")

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

    # If requested, print a Jenkins snippet and exit
    if args.jenkins_snippet:
        snippet = f"""
Recommended Jenkins usage examples:

Shell (Linux/Mac):
  python baselineintelligence/readiness_score.py --build-id "$BUILD_TAG"

Windows batch / PowerShell:
  python baselineintelligence/readiness_score.py --build-id "%BUILD_TAG%"

Declarative Pipeline (example):
pipeline {
  agent any
  stages {
    stage('Compute Readiness') {
      steps {
        sh 'python baselineintelligence/readiness_score.py --build-id "$BUILD_TAG"'
      }
    }
  }
}

This script will also pick up BUILD_NUMBER and JOB_NAME automatically when run inside Jenkins.
"""
        print(snippet)
        return

    # Overwrite-by-timestamp approach: if an existing point for the same identifier exists, write using its timestamp so InfluxDB updates the point (preserving history semantics differently)
    existing_point_time = None
    try:
        # Query the most recent point for this identifier
        latest_query = f"SELECT * FROM \"aiperf_release_readiness\" WHERE {where_clause} ORDER BY time DESC LIMIT 1"
        latest = local_client.query(latest_query)
        for measurement, points in latest.items():
            for row in points:
                # InfluxDB returns 'time' for points
                t = row.get('time')
                if t:
                    existing_point_time = t
                    break
            if existing_point_time:
                break
    except Exception as e:
        logger.warning(f"Could not fetch existing readiness point for {identifier_expr}: {e}")

    if args.dry_run:
        logger.info("Dry run enabled - not writing to InfluxDB. The following point would be written:")
        if existing_point_time:
            print(f"Would overwrite existing point at time: {existing_point_time} (identifier={identifier_expr})")
            # show the point with the time that would be used
            sample = json_body.copy()
            sample[0] = sample[0].copy()
            sample[0]['time'] = existing_point_time
            print(sample)
        else:
            print(json_body)
        print(f"Deduplication mode: {dedupe_mode}")
        return

    try:
        if existing_point_time:
            # Overwrite by writing the point with the same timestamp as the existing point
            json_body[0]['time'] = existing_point_time
            logger.info(f"Overwriting existing readiness point at time {existing_point_time} for {identifier_expr}")
        else:
            logger.info(f"No existing readiness point found for {identifier_expr}; writing a new point")

        local_client.write_points(json_body)
        logger.info("Stored release readiness in InfluxDB (overwrite-by-timestamp)")
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


if __name__ == "__main__":
    run_readiness()

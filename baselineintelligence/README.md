AiPERF Baseline Intelligence - Readiness Integration

This document explains how to integrate the readiness_score.py script into Jenkins and how the deduplication/overwrite logic works.

Usage
------

- The script computes a release readiness score based on the latest aiperf_analysis points in InfluxDB and writes the result to the aiperf_release_readiness measurement.

- Recommended invocation from Jenkins (pass a stable build identifier so the script can deduplicate):

  sh 'python baselineintelligence/readiness_score.py --build-id "$BUILD_TAG"'

Environment variables the script reads (automatically provided by Jenkins):
- BUILD_ID: legacy build id
- BUILD_NUMBER: incremental build number
- JOB_NAME: Jenkins job name

Command-line options
----------------------
- --build-id: Explicit build identifier (overrides BUILD_ID environment variable)
- --dry-run: Do not perform delete/write; only print what would be done
- --jenkins-snippet: Print an example Jenkinsfile snippet and exit

Deduplication and overwrite semantics
-------------------------------------
- When invoked with a stable build identifier (either via --build-id or BUILD_ID env var), the script will deduplicate by build_id.
- If BUILD_ID is not supplied but JOB_NAME and BUILD_NUMBER are present, the script will deduplicate using the job_name+build_number pair.
- If none of the above are available, the script uses an internally generated run_id (timestamp) and will not deduplicate across different runs.

Overwrite-by-timestamp behavior:
- The script searches for the most recent readiness point that matches the chosen identifier. If one exists, the script writes the new readiness point using the same timestamp as the existing point — this causes InfluxDB to overwrite that point (same measurement+tags+timestamp).
- If no existing point is found, the script writes a new point.

Dry-run and testing
--------------------
- Use --dry-run to see what would be deleted/overwritten without touching InfluxDB.

Jenkins example
----------------
See Jenkinsfile.example in the repository root for an example Declarative Pipeline stage.

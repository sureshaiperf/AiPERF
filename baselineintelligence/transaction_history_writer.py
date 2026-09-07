import os
import sys

from influxdb import InfluxDBClient
from jmeter_run_metrics import read_jtl

# =====================================================
# RUN ID
# =====================================================

run_id = None
run_start_epoch = os.getenv("RUN_START_EPOCH")
run_end_epoch = os.getenv("RUN_END_EPOCH")

if len(sys.argv) > 1:
    run_id = sys.argv[1]
else:
    run_id = os.getenv("RUN_ID")

if not run_id:
    raise Exception(
        "RUN_ID not provided via argument or environment variable"
    )

if not run_start_epoch or not run_end_epoch:
    raise RuntimeError(
        "RUN_START_EPOCH and RUN_END_EPOCH are required for run-scoped metrics"
    )

print(f"Using RUN_ID = {run_id}")

# =====================================================
# INFLUXDB CONNECTION
# =====================================================

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

# =====================================================
# DUPLICATE CHECK
# =====================================================

dup_query = f"""
SELECT *
FROM aiperf_transaction_history
WHERE run_id='{run_id}'
LIMIT 1
"""

dup_result = client.query(dup_query)

if list(dup_result.get_points()):

    print(
        f"Transaction history already exists "
        f"for {run_id}. Skipping."
    )

    sys.exit(0)

jtl_path = os.getenv("JTL_PATH")
if jtl_path and os.path.exists(jtl_path):
    metrics = read_jtl(jtl_path)
    records = []
    for transaction, data in metrics.items():
        if transaction == "all":
            continue
        records.append(
            {
                "measurement": "aiperf_transaction_history",
                "tags": {"run_id": run_id, "transaction": transaction},
                "fields": data,
            }
        )
    if records:
        client.write_points(records)
    print(f"Transactions Captured : {len(records)}")
    print("Transaction History Written Successfully.")
    sys.exit(0)

# =====================================================
# FETCH TRANSACTION METRICS
# =====================================================

query = f"""
SELECT
    LAST(avg) AS avg_rt,
    LAST(count) AS samples,
    LAST(countError) AS errors,
    LAST("pct50.0") AS p50,
    LAST("pct95.0") AS p95,
    LAST("pct99.0") AS p99
FROM jmeter
WHERE time >= {int(run_start_epoch)}ms
AND time <= {int(run_end_epoch)}ms
AND transaction != 'all'
AND transaction != 'internal'
AND statut='all'
GROUP BY transaction
"""

result = client.query(query)

# =====================================================
# VALIDATION
# =====================================================

if "series" not in result.raw:

    print("No transaction data found.")

    sys.exit(0)

# =====================================================
# WRITE TRANSACTION HISTORY
# =====================================================

records_written = 0

for series in result.raw["series"]:

    transaction_name = (
        series.get("tags", {})
        .get("transaction", "UNKNOWN")
    )

    values = series.get("values", [])

    if not values:
        continue

    row = values[0]

    columns = series["columns"]

    data = dict(zip(columns, row))

    avg_rt = float(
    data.get("avg_rt") or 0
    )

    samples = float(
        data.get("samples") or 0
    )

    errors = float(
        data.get("errors") or 0
    )

    p95 = float(
        data.get("p95") or 0
    )

    p99 = float(
        data.get("p99") or 0
    )

    if samples > 0:

        error_pct = round(
            (errors / samples) * 100,
            4
        )

    else:

        error_pct = 0.0

    print(
        f"Writing Transaction : "
        f"{transaction_name}"
    )

    json_body = [{
        "measurement":
            "aiperf_transaction_history",

        "tags": {
            "run_id": run_id,
            "transaction": transaction_name
        },

        "fields": {
            "samples": samples,
            "avg_rt": avg_rt,
            "p95": p95,
            "p99": p99,
            "errors": errors,
            "error_pct": error_pct
        }
    }]

    client.write_points(json_body)

    records_written += 1

# =====================================================
# SUMMARY
# =====================================================

print("\n===================================")
print("TRANSACTION HISTORY WRITER")
print("===================================")

print(f"RUN_ID                : {run_id}")
print(f"Transactions Captured : {records_written}")

print(
    "\nTransaction History Written Successfully."
)
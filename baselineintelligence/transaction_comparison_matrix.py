import pandas as pd
from influxdb import InfluxDBClient
import os
import sys
from baseline_selection import BaselineSelectionError, select_comparison_run

# =====================================================
# CONFIGURATION
# =====================================================

INFLUX_HOST = "localhost"
INFLUX_PORT = 8086
INFLUX_DB = "jmeter"

# =====================================================
# CONNECT
# =====================================================

client = InfluxDBClient(
    host=INFLUX_HOST,
    port=INFLUX_PORT,
    database=INFLUX_DB
)

print("\n===================================")
print("AIPERF TRANSACTION COMPARISON")
print("===================================\n")

# =====================================================
# LOAD TRANSACTION HISTORY
# =====================================================

result = client.query("""
SELECT *
FROM aiperf_transaction_history
""")

txn_df = pd.DataFrame(
    list(result.get_points())
)

if txn_df.empty:
    print("No transaction history found.")
    exit(0)

# =====================================================
# IDENTIFY LATEST 2 RUNS
# =====================================================

# Order runs by when they actually happened (InfluxDB point time), not by
# sorting the run_id string -- "RUN_10_..." sorts before "RUN_9_..." as text,
# which silently broke "prior run" lookups once build numbers reached double
# digits.
run_order = (
    txn_df.assign(time=pd.to_datetime(txn_df["time"]))
    .groupby("run_id")["time"]
    .min()
    .sort_values()
)
run_ids = run_order.index.tolist()

if len(run_ids) < 2:
    print("Minimum 2 runs required.")
    exit(0)

current_run_id = sys.argv[1] if len(sys.argv) > 1 else os.getenv("RUN_ID")
if not current_run_id:
    raise ValueError("RUN_ID must be provided as the first CLI argument or environment variable")
if current_run_id not in run_order.index:
    print(f"Could not find RUN_ID={current_run_id} and a prior run.")
    exit(0)
prior_runs = [
    run_id for run_id in run_ids
    if run_order[run_id] < run_order[current_run_id]
]
try:
    selection = select_comparison_run(client, current_run_id, prior_runs)
except BaselineSelectionError as exc:
    print(f"Baseline Selection Failed: {exc}")
    client.close()
    raise SystemExit(2)
comparison_run_id = selection.comparison_run_id
print(f"Selection Status : {selection.selection_status}")
print(f"Selection Mode   : {selection.selection_mode}")
print(f"Fallback Used    : {str(selection.fallback_used).lower()}")

print(
    f"Comparing {current_run_id} "
    f"vs {comparison_run_id}"
)

# =====================================================
# REMOVE EXISTING RECORDS
# =====================================================

try:
    client.query(
        f"""
        DROP SERIES
        FROM aiperf_transaction_comparison
        WHERE current_run_id='{current_run_id}'
        """
    )
except Exception:
    pass

# =====================================================
# METRICS TO COMPARE
# =====================================================

metrics = [
    "avg_rt",
    "p95",
    "p99",
    "error_pct"
]

# =====================================================
# BUILD COMPARISON RECORDS
# =====================================================

records = []

for transaction in sorted(
    txn_df["transaction"].unique()
):

    current_df = txn_df[
        (txn_df["run_id"] == current_run_id)
        &
        (txn_df["transaction"] == transaction)
    ]

    previous_df = txn_df[
        (txn_df["run_id"] == comparison_run_id)
        &
        (txn_df["transaction"] == transaction)
    ]

    if current_df.empty:
        continue

    if previous_df.empty:
        continue

    current_row = current_df.iloc[0]
    previous_row = previous_df.iloc[0]

    print("\n-----------------------------------")
    print(f"Transaction : {transaction}")
    print("-----------------------------------")

    for metric in metrics:

        current_value = float(
            current_row.get(metric, 0.0) or 0.0
        )

        previous_value = float(
            previous_row.get(metric, 0.0) or 0.0
        )

        if previous_value == 0:
            variance_pct = 0.0
        else:
            variance_pct = round(
                (
                    (
                        current_value
                        - previous_value
                    )
                    / previous_value
                ) * 100,
                2
            )

        print(
            f"{metric:<12}"
            f"{current_value:<15.2f}"
            f"{previous_value:<15.2f}"
            f"{variance_pct:<10.2f}"
        )

        records.append({
            "measurement":
                "aiperf_transaction_comparison",

            "tags": {
                "current_run_id":
                    current_run_id,

                "comparison_run_id":
                    comparison_run_id,

                "transaction":
                    transaction,

                "metric":
                    metric
            },

            "fields": {
                "current_value":
                    float(current_value),

                "previous_value":
                    float(previous_value),

                "variance_pct":
                    float(variance_pct)
            }
        })

# =====================================================
# WRITE TO INFLUXDB
# =====================================================

if records:

    client.write_points(records)

# =====================================================
# SUMMARY
# =====================================================

print("\n===================================")
print("TRANSACTION COMPARISON COMPLETED")
print("===================================")

print(
    f"Current Run      : {current_run_id}"
)

print(
    f"Comparison Run   : {comparison_run_id}"
)

print(
    f"Records Written  : {len(records)}"
)

print(
    "Measurement      : "
    "aiperf_transaction_comparison"
)
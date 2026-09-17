import pandas as pd
from influxdb import InfluxDBClient
import os
import sys

# =====================================================
# CONFIG
# =====================================================

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

# =====================================================
# LOAD TRANSACTION HISTORY
# =====================================================

txn_result = client.query(
    """
    SELECT *
    FROM aiperf_transaction_history
    """
)

txn_df = pd.DataFrame(
    list(txn_result.get_points())
)

if txn_df.empty:

    print(
        "No Transaction History Found."
    )

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

    print(
        "Minimum 2 executions required."
    )

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
if not prior_runs:
    print(f"Could not find RUN_ID={current_run_id} and a prior run.")
    exit(0)
comparison_run_id = prior_runs[-1]

print(
    f"Comparing "
    f"{current_run_id}"
    f" vs "
    f"{comparison_run_id}"
)

# =====================================================
# TRANSACTION COMPARISON
# =====================================================

transaction_metrics = [

    "avg_rt",
    "p95",
    "p99",
    "error_pct"
]

for transaction in txn_df["transaction"].unique():

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

    if current_df.empty or previous_df.empty:
        continue

    current_row = current_df.iloc[0]
    previous_row = previous_df.iloc[0]

    for metric in transaction_metrics:

        current_value = float(
            current_row[metric] or 0.0
        )

        historical_value = float(
            previous_row[metric] or 0.0
        )

        if historical_value == 0:

            variance_pct = 0.0

        else:

            variance_pct = round(
                (
                    (
                        current_value
                        -
                        historical_value
                    )
                    /
                    historical_value
                )
                * 100,
                2
            )

        json_body = [{

            "measurement":
                "aiperf_run_comparison",

            "tags": {

                "current_run_id":
                    current_run_id,

                "comparison_run_id":
                    comparison_run_id,

                "entity_type":
                    "transaction",

                "entity_name":
                    transaction,

                "metric":
                    metric
            },

            "fields": {

                "current_value":
                    float(current_value),

                "historical_value":
                    float(historical_value),

                "variance_pct":
                    float(variance_pct)
            }
        }]

        client.write_points(json_body)

# =====================================================
# SERVICE COMPARISON
# =====================================================

service_result = client.query(
    """
    SELECT *
    FROM aiperf_service_metrics
    """
)

service_df = pd.DataFrame(
    list(service_result.get_points())
)

if not service_df.empty:

    service_metrics = [
    "request_count",
    "avg_response_time_ms",
    "active_requests",
    "executor_active",
    "heap_pct",
    "gc_overhead"
]

    for service in service_df[
        "service_name"
    ].unique():

        current_df = service_df[
            (service_df["run_id"] == current_run_id)
            &
            (
                service_df[
                    "service_name"
                ] == service
            )
        ]

        previous_df = service_df[
            (service_df["run_id"] == comparison_run_id)
            &
            (
                service_df[
                    "service_name"
                ] == service
            )
        ]

        if current_df.empty or previous_df.empty:
            continue

        current_row = current_df.iloc[0]
        previous_row = previous_df.iloc[0]

        for metric in service_metrics:
            if metric == "request_count" and not (
                pd.notna(current_row.get("request_count_available"))
                and pd.notna(previous_row.get("request_count_available"))
                and bool(current_row.get("request_count_available"))
                and bool(previous_row.get("request_count_available"))
            ):
                continue

            if pd.isna(current_row.get(metric)) or pd.isna(previous_row.get(metric)):
                continue

            current_value = float(
                current_row.get(metric, 0)
            )

            historical_value = float(
                previous_row[metric] or 0.0
            )

            if historical_value == 0:

                variance_pct = 0.0

            else:

                variance_pct = round(
                    (
                        (
                            current_value
                            -
                            historical_value
                        )
                        /
                        historical_value
                    )
                    * 100,
                    2
                )

            json_body = [{

                "measurement":
                    "aiperf_run_comparison",

                "tags": {

                    "current_run_id":
                        current_run_id,

                    "comparison_run_id":
                        comparison_run_id,

                    "entity_type":
                        "service",

                    "entity_name":
                        service,

                    "metric":
                        metric
                },

                "fields": {

                    "current_value":
                        float(current_value),

                    "historical_value":
                        float(historical_value),

                    "variance_pct":
                        float(variance_pct)
                }
            }]

            client.write_points(
                json_body
            )

print()
print("===================================")
print("RUN COMPARISON COMPLETED")
print("===================================")
print(
    f"Current Run    : {current_run_id}"
)
print(
    f"Compared With  : {comparison_run_id}"
)
print(
    "Measurement    : aiperf_run_comparison"
)
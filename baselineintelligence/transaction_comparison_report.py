import pandas as pd

from influxdb import InfluxDBClient

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

run_ids = (
    txn_df["run_id"]
    .drop_duplicates()
    .tolist()
)

run_ids = sorted(run_ids)

if len(run_ids) < 2:

    print(
        "Minimum 2 executions required."
    )

    exit(0)

comparison_run_id = run_ids[-2]
current_run_id = run_ids[-1]

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

        "system_cpu_usage",

        "process_cpu_usage",

        "jvm_memory_used_mb",

        "jvm_threads_live",

        "http_server_requests"
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
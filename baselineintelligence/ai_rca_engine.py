from influxdb import InfluxDBClient
import pandas as pd
import os
import sys

# =====================================================
# CONFIGURATION
# =====================================================

INFLUX_HOST = "localhost"
INFLUX_PORT = 8086
INFLUX_DB = "jmeter"

client = InfluxDBClient(
    host=INFLUX_HOST,
    port=INFLUX_PORT,
    database=INFLUX_DB
)

print("\n===================================")
print("AiPERF RCA ENGINE")
print("===================================")

# =====================================================
# GET LATEST RUN
# =====================================================

run_id = sys.argv[1] if len(sys.argv) > 1 else os.getenv("RUN_ID")
if not run_id:
    raise ValueError("RUN_ID must be provided as the first CLI argument or environment variable")

query = f"""
SELECT *
FROM aiperf_run_comparison
WHERE current_run_id='{run_id}'
ORDER BY time DESC
LIMIT 1
"""

rows = list(client.query(query).get_points())

if not rows:
    raise Exception(
        "No run comparison data found"
    )

latest_run = run_id

print(f"\nCurrent Run : {latest_run}")

# =====================================================
# TRANSACTION COMPARISON
# =====================================================

txn_query = f"""
SELECT *
FROM aiperf_transaction_comparison
WHERE current_run_id='{latest_run}'
"""

txn_df = pd.DataFrame(
    list(client.query(txn_query).get_points())
)

if txn_df.empty:
    raise Exception(
        "No transaction comparison data found"
    )

txn_df["abs_variance"] = (
    txn_df["variance_pct"]
    .abs()
)

top_txn = txn_df.sort_values(
    by="abs_variance",
    ascending=False
).iloc[0]

txn_name = top_txn["transaction"]
txn_metric = top_txn["metric"]
txn_variance = round(
    float(top_txn["variance_pct"]),
    2
)

# =====================================================
# SERVICE COMPARISON
# =====================================================

svc_query = f"""
SELECT *
FROM aiperf_service_comparison
WHERE current_run_id='{latest_run}'
"""

svc_df = pd.DataFrame(
    list(client.query(svc_query).get_points())
)

if svc_df.empty:
    raise Exception(
        "No service comparison data found"
    )

svc_records = []

for _, row in svc_df.iterrows():

    svc_records.append(
        (
            row["service_name"],
            "request_count",
            abs(
                float(
                    row.get(
                        "request_count_variance_pct",
                        0
                    )
                )
            )
        )
    )

    svc_records.append(
        (
            row["service_name"],
            "avg_rt",
            abs(
                float(
                    row.get(
                        "avg_rt_variance_pct",
                        0
                    )
                )
            )
        )
    )

    svc_records.append(
        (
            row["service_name"],
            "heap_pct",
            abs(
                float(
                    row.get(
                        "heap_pct_variance_pct",
                        0
                    )
                )
            )
        )
    )

    svc_records.append(
        (
            row["service_name"],
            "gc_overhead",
            abs(
                float(
                    row.get(
                        "gc_variance_pct",
                        0
                    )
                )
            )
        )
    )

svc_df2 = pd.DataFrame(
    svc_records,
    columns=[
        "service",
        "metric",
        "variance"
    ]
)

top_service = svc_df2.sort_values(
    by="variance",
    ascending=False
).iloc[0]

service_name = top_service["service"]
service_metric = top_service["metric"]
service_variance = round(
    float(top_service["variance"]),
    2
)

# =====================================================
# RCA GENERATION
# =====================================================

rca_text = f"""
Top transaction regression detected in
{txn_name} ({txn_metric})
with variance of {txn_variance:.2f}%.

Most impacted service is
{service_name}
showing {service_metric}
variance of {service_variance:.2f}%.

Possible root cause:
Transaction degradation appears correlated
with behavioral changes observed in
{service_name}.

Recommendation:
Investigate service latency,
request volume growth,
resource utilization,
and downstream dependencies.
"""

print("\n===================================")
print("RCA GENERATED")
print("===================================")

print(rca_text)

# =====================================================
# WRITE RCA
# =====================================================

json_body = [{
    "measurement": "aiperf_ai_insights",

    "tags": {
        "run_id": latest_run,
        "insight_type": "rca"
    },

    "fields": {

        "transaction_name":
            str(txn_name),

        "transaction_metric":
            str(txn_metric),

        "transaction_variance_pct":
            float(txn_variance),

        "service_name":
            str(service_name),

        "service_metric":
            str(service_metric),

        "service_variance_pct":
            float(service_variance),

        "rca_text":
            str(rca_text)
    }
}]

client.write_points(json_body)

print("\n===================================")
print("RCA WRITTEN TO aiperf_ai_insights")
print("===================================")
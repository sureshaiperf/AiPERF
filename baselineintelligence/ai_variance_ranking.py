import pandas as pd
from influxdb import InfluxDBClient
import os
import sys

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
print("AiPERF VARIANCE RANKING")
print("===================================\n")

# =====================================================
# LOAD COMPARISON DATA
# =====================================================

result = client.query("""
SELECT *
FROM aiperf_run_comparison
""")

df = pd.DataFrame(
    list(result.get_points())
)

if df.empty:

    print("No comparison data found.")
    exit(0)

# =====================================================
# FIND LATEST RUN
# =====================================================

latest_run = sys.argv[1] if len(sys.argv) > 1 else os.getenv("RUN_ID")
if not latest_run:
    raise ValueError("RUN_ID must be provided as the first CLI argument or environment variable")
if str(latest_run) not in df["current_run_id"].astype(str).values:
    print(f"No comparison data found for RUN_ID={latest_run}.")
    exit(0)

print(f"Latest Run : {latest_run}")

latest_df = df[
    df["current_run_id"] == latest_run
].copy()

# =====================================================
# REMOVE NOISY METRICS
# =====================================================

transaction_exclusions = [
    "samples",
    "errors"
]

service_exclusions = [
    "http_server_requests"
]

latest_df = latest_df[
    ~(
        (latest_df["entity_type"] == "transaction")
        &
        (
            latest_df["metric"].isin(
                transaction_exclusions
            )
        )
    )
]

latest_df = latest_df[
    ~(
        (latest_df["entity_type"] == "service")
        &
        (
            latest_df["metric"].isin(
                service_exclusions
            )
        )
    )
]

# =====================================================
# ABSOLUTE VARIANCE
# =====================================================

latest_df["abs_variance"] = (
    latest_df["variance_pct"]
    .abs()
)

latest_df = latest_df.sort_values(
    by="abs_variance",
    ascending=False
).reset_index(drop=True)

latest_df["rank"] = (
    latest_df.index + 1
)

# =====================================================
# CLEAN EXISTING RANKINGS
# =====================================================

client.query(
    f"""
    DROP SERIES
    FROM aiperf_variance_ranking
    WHERE run_id='{latest_run}'
    """
)

# =====================================================
# BUILD WRITE PAYLOAD
# =====================================================

json_body = []

for _, row in latest_df.iterrows():

    json_body.append({

        "measurement":
            "aiperf_variance_ranking",

        "tags": {

            "run_id":
                str(row["current_run_id"]),

            "entity_type":
                str(row["entity_type"]),

            "entity_name":
                str(row["entity_name"]),

            "metric":
                str(row["metric"])
        },

        "fields": {

            "variance_pct":
                float(row["variance_pct"]),

            "rank":
                int(row["rank"])
        }
    })

# =====================================================
# WRITE TO INFLUXDB
# =====================================================

if json_body:

    client.write_points(json_body)

# =====================================================
# DISPLAY TOP 10
# =====================================================

print("\n===================================")
print("TOP 10 REGRESSIONS")
print("===================================\n")

top10 = latest_df.head(10)

for _, row in top10.iterrows():

    print(
        f"Rank {int(row['rank'])} | "
        f"{row['entity_type']} | "
        f"{row['entity_name']} | "
        f"{row['metric']} | "
        f"{float(row['variance_pct']):.2f}%"
    )

# =====================================================
# SUMMARY
# =====================================================

print("\n===================================")
print("VARIANCE RANKING COMPLETED")
print("===================================")

print(f"Run ID          : {latest_run}")
print(f"Records Written : {len(json_body)}")
print("Measurement     : aiperf_variance_ranking")

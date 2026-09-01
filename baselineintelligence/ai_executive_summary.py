import pandas as pd
from influxdb import InfluxDBClient

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

result = client.query("""
SELECT *
FROM aiperf_variance_ranking
""")

df = pd.DataFrame(
    list(result.get_points())
)

if df.empty:

    print("No ranking data found")
    exit(0)

latest_run = (
    df["run_id"]
    .sort_values()
    .iloc[-1]
)

latest_df = df[
    df["run_id"] == latest_run
]

transactions = latest_df[
    latest_df["entity_type"]
    == "transaction"
]

services = latest_df[
    latest_df["entity_type"]
    == "service"
]

top_transaction = (
    transactions
    .sort_values(
        by="variance_pct",
        key=abs,
        ascending=False
    )
    .head(1)
)

top_service = (
    services
    .sort_values(
        by="variance_pct",
        key=abs,
        ascending=False
    )
    .head(1)
)

insights = []

if not top_transaction.empty:

    row = top_transaction.iloc[0]

    insights.append(
        f"Top Transaction Regression: "
        f"{row['entity_name']} "
        f"{row['metric']} "
        f"{row['variance_pct']:.2f}%"
    )

if not top_service.empty:

    row = top_service.iloc[0]

    insights.append(
        f"Top Service Regression: "
        f"{row['entity_name']} "
        f"{row['metric']} "
        f"{row['variance_pct']:.2f}%"
    )

for insight in insights:

    json_body = [{

        "measurement":
            "aiperf_ai_insights",

        "tags": {

            "run_id":
                latest_run,

            "insight_type":
                "executive_summary"
        },

        "fields": {

            "insight_text":
                insight
        }
    }]

    client.write_points(json_body)

print(
    f"Executive Summary Created for {latest_run}"
)

for insight in insights:
    print(insight)
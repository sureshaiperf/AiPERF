import pandas as pd
import numpy as np
import sys
import os

from influxdb import InfluxDBClient
from sklearn.preprocessing import StandardScaler
from sklearn.metrics.pairwise import cosine_similarity

# =====================================================
# InfluxDB Connection
# =====================================================

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

# =====================================================
# Read Fingerprints
# =====================================================

result = client.query(
    "SELECT * FROM aiperf_execution_fingerprint"
)

points = list(result.get_points())

df = pd.DataFrame(points)

if len(df) < 5:
    print("\n===================================")
    print("SIMILAR EXECUTION INTELLIGENCE")
    print("===================================")
    print(
        f"WARNING: Only {len(df)} execution(s) available. "
        "Minimum 5 executions required for similarity analysis."
    )
    print("Skipping Similar Execution Intelligence.")
    sys.exit(0)

print(f"Total Fingerprints Found : {len(df)}")

# =====================================================
# Feature Columns
# =====================================================

feature_columns = [

    "avg_rt",
    "p95",
    "p99",
    "throughput",
    "error_rate",

    "host_system_cpu",

    "gateway_process_cpu",
    "gateway_jvm_memory",
    "gateway_jvm_threads",
    "gateway_http_requests",

    "user_process_cpu",
    "user_jvm_memory",
    "user_jvm_threads",
    "user_http_requests",

    "product_process_cpu",
    "product_jvm_memory",
    "product_jvm_threads",
    "product_http_requests",

    "order_process_cpu",
    "order_jvm_memory",
    "order_jvm_threads",
    "order_http_requests"
]

# =====================================================
# Validate Columns
# =====================================================

missing_columns = [
    c for c in feature_columns
    if c not in df.columns
]

if missing_columns:

    raise Exception(
        f"Missing fingerprint columns: {missing_columns}"
    )

# =====================================================
# Preserve Original Values
# =====================================================

original_df = df.copy()

# =====================================================
# Prepare Data For Similarity
# =====================================================

df[feature_columns] = (
    df[feature_columns]
    .fillna(0)
    .astype(float)
)

scaler = StandardScaler()

df[feature_columns] = scaler.fit_transform(
    df[feature_columns]
)

df[feature_columns] = df[feature_columns].astype(float)

# =====================================================
# Sort By Time
# =====================================================

df = df.sort_values("time")
original_df = original_df.sort_values("time")

# =====================================================
# Current Execution
# =====================================================

current_run_id = sys.argv[1] if len(sys.argv) > 1 else os.getenv("RUN_ID")
if not current_run_id:
    raise ValueError("RUN_ID must be provided as the first CLI argument or environment variable")
current_rows = df[df["run_id"].astype(str) == str(current_run_id)]
if current_rows.empty:
    raise ValueError(f"No fingerprint found for RUN_ID={current_run_id}")
current_run = current_rows.iloc[-1]
current_original = original_df[original_df["run_id"].astype(str) == str(current_run_id)].iloc[-1]

print(f"Current Run : {current_run_id}")

current_vector = current_run[
    feature_columns
].values.reshape(1, -1)

# =====================================================
# Similarity Calculation
# =====================================================

similarity_results = []

historical_df = df[df["run_id"].astype(str) != str(current_run_id)]
historical_original_df = original_df[original_df["run_id"].astype(str) != str(current_run_id)]
for idx in range(len(historical_df)):

    historical_run = historical_df.iloc[idx]
    historical_original = historical_original_df.iloc[idx]

    historical_vector = historical_run[
        feature_columns
    ].values.reshape(1, -1)

    raw_score = cosine_similarity(
        current_vector,
        historical_vector
    )[0][0]

    similarity_score = round(
        ((raw_score + 1) / 2) * 100,
        2
    )

    similarity_results.append({

        "current_run_id":
            current_run_id,

        "similar_run_id":
            historical_original["run_id"],

        "similarity_score":
            similarity_score,

        "current_avg_rt":
            float(current_original["avg_rt"]),

        "historical_avg_rt":
            float(historical_original["avg_rt"]),

        "current_p95":
            float(current_original["p95"]),

        "historical_p95":
            float(historical_original["p95"]),

        "current_throughput":
            float(current_original["throughput"]),

        "historical_throughput":
            float(historical_original["throughput"])
    })

# =====================================================
# Top 3 Matches
# =====================================================

result_df = pd.DataFrame(similarity_results)

result_df = result_df.sort_values(
    by="similarity_score",
    ascending=False
)

top3 = result_df.head(3)

print("\n===================================")
print("TOP 3 SIMILAR EXECUTIONS")
print("===================================")

print(
    top3[
        [
            "similar_run_id",
            "similarity_score"
        ]
    ].to_string(index=False)
)

# =====================================================
# Save To InfluxDB
# =====================================================

for _, row in top3.iterrows():

    json_body = [{
        "measurement": "aiperf_similar_execution",

        "tags": {
            "current_run_id":
                row["current_run_id"],

            "similar_run_id":
                row["similar_run_id"]
        },

        "fields": {
            "similarity_score":
                float(row["similarity_score"])
        }
    }]

    client.write_points(json_body)

# =====================================================
# AI Insight
# =====================================================

best_match = top3.iloc[0]

avg_rt_change = round(
    (
        (
            best_match["current_avg_rt"]
            - best_match["historical_avg_rt"]
        )
        /
        max(best_match["historical_avg_rt"], 1)
    ) * 100,
    2
)

p95_change = round(
    (
        (
            best_match["current_p95"]
            - best_match["historical_p95"]
        )
        /
        max(best_match["historical_p95"], 1)
    ) * 100,
    2
)

throughput_change = round(
    (
        (
            best_match["current_throughput"]
            - best_match["historical_throughput"]
        )
        /
        max(best_match["historical_throughput"], 1)
    ) * 100,
    2
)

if p95_change > 20:

    insight = (
        f"Potential performance regression detected. "
        f"P95 increased by {p95_change}% compared to "
        f"the closest historical execution."
    )

elif p95_change < -20:

    insight = (
        f"Performance improvement detected. "
        f"P95 improved by {abs(p95_change)}% compared to "
        f"the closest historical execution."
    )

else:

    insight = (
        "Performance behaviour is consistent with "
        "the closest historical execution."
    )

# =====================================================
# Display Results
# =====================================================

print("\n===================================")
print("AI INSIGHT")
print("===================================")

print(
    f"Current Run          : {current_run_id}"
)

print(
    f"Closest Match        : {best_match['similar_run_id']}"
)

print(
    f"Similarity Score     : {best_match['similarity_score']}%"
)

print(
    f"Current Avg RT       : {best_match['current_avg_rt']}"
)

print(
    f"Historical Avg RT    : {best_match['historical_avg_rt']}"
)

print(
    f"Avg RT Change (%)    : {avg_rt_change}"
)

print(
    f"Current P95          : {best_match['current_p95']}"
)

print(
    f"Historical P95       : {best_match['historical_p95']}"
)

print(
    f"P95 Change (%)       : {p95_change}"
)

print(
    f"Current Throughput   : {best_match['current_throughput']}"
)

print(
    f"Historical Throughput: {best_match['historical_throughput']}"
)

print(
    f"Throughput Change (%) : {throughput_change}"
)

print(
    f"AI Insight           : {insight}"
)

print("\nSimilarity Analysis Completed Successfully.")
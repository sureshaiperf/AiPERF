from influxdb import DataFrameClient
from sklearn.preprocessing import StandardScaler
from sklearn.metrics.pairwise import cosine_similarity

# --------------------------------------------------
# InfluxDB Connection
# --------------------------------------------------

client = DataFrameClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

# --------------------------------------------------
# Load Historical Execution Summaries
# --------------------------------------------------

query = """
SELECT avg,
       "pct90.0",
       "pct95.0",
       "pct99.0",
       count,
       countError
FROM jmeter
WHERE transaction='all'
"""

result = client.query(query)

df = result["jmeter"]

print(f"\nHistorical Records: {len(df)}")
print("\nFirst Record :", df.index.min())
print("Last Record  :", df.index.max())

# --------------------------------------------------
# Current Execution (Latest Record)
# --------------------------------------------------

current_run = df.tail(1).copy()

# Historical records excluding current

historical_df = df.iloc[:-1].copy()

# --------------------------------------------------
# Features Used For Similarity
# --------------------------------------------------

features = [
    "avg",
    "pct90.0",
    "pct95.0",
    "pct99.0",
    "count",
    "countError"
]

# --------------------------------------------------
# Standardize Data
# --------------------------------------------------

scaler = StandardScaler()

historical_scaled = scaler.fit_transform(
    historical_df[features]
)

current_scaled = scaler.transform(
    current_run[features]
)

# --------------------------------------------------
# Display Current Execution
# --------------------------------------------------

curr = current_run.iloc[0]
print("\n=== Current Execution ===")
print(f"""
Time : {current_run.index[0]}
Avg RT : {curr['avg']:.2f}
P90 : {curr['pct90.0']:.2f}
P95 : {curr['pct95.0']:.2f}
P99 : {curr['pct99.0']:.2f}
Requests : {curr['count']:.0f}
Errors : {curr['countError']:.0f}
""")

# --------------------------------------------------
# Calculate Cosine Similarity
# --------------------------------------------------

similarities = cosine_similarity(
    current_scaled,
    historical_scaled
)[0]

historical_df["similarity"] = similarities * 100

# --------------------------------------------------
# Top 5 Similar Executions
# --------------------------------------------------

top5 = historical_df.sort_values(
    by="similarity",
    ascending=False
).head(5)

# --------------------------------------------------
# Display Results
# --------------------------------------------------

print("\n=== Top 5 Similar Executions ===")

for idx, row in top5.iterrows():

    print(f"""
Time        : {idx}
Similarity  : {row['similarity']:.2f}%

Avg RT      : {row['avg']:.2f}
P90         : {row['pct90.0']:.2f}
P95         : {row['pct95.0']:.2f}
P99         : {row['pct99.0']:.2f}

Requests    : {row['count']:.0f}
Errors      : {row['countError']:.0f}
""")
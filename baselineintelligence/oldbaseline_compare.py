from influxdb import InfluxDBClient

print("===== AiPERF Baseline Intelligence =====")

client = InfluxDBClient(
    host='localhost',
    port=8086,
    database='jmeter'
)

# -------------------------------
# Read Baseline
# -------------------------------

baseline_query = """
SELECT LAST("p95")
FROM "aiperf_baseline"
GROUP BY "transaction"
"""

baseline_result = client.query(baseline_query)

baseline_map = {}

for measurement, points in baseline_result.items():

    transaction = measurement[1].get("transaction")

    for row in points:
        baseline_map[transaction] = row["last"]

# -------------------------------
# Read Current Metrics
# -------------------------------

current_query = """
SELECT LAST("pct95.0")
FROM "jmeter"
GROUP BY "transaction"
"""

current_result = client.query(current_query)

analysis_points = []

# -------------------------------
# Compare
# -------------------------------

for measurement, points in current_result.items():

    transaction = measurement[1].get("transaction")

    for row in points:

        current_p95 = row["last"]

        baseline_p95 = baseline_map.get(transaction)

        if baseline_p95 is None:
            continue

        deviation = (
            (current_p95 - baseline_p95)
            / baseline_p95
        ) * 100

        if deviation <= 10:
            status = "PASS"

        elif deviation <= 20:
            status = "WARNING"

        else:
            status = "FAIL"

        print("\n--------------------------------")
        print(f"Transaction : {transaction}")
        print(f"Baseline P95 : {baseline_p95:.2f}")
        print(f"Current P95  : {current_p95:.2f}")
        print(f"Deviation %  : {deviation:.2f}")
        print(f"Status       : {status}")

        analysis_points.append({
            "measurement": "aiperf_analysis",

            "tags": {
                "transaction": transaction,
                "status": status
            },

            "fields": {
                "baseline_p95": round(baseline_p95,2),
                "current_p95": round(current_p95,2),
                "deviation": round(deviation,2)
            }
        })

# -------------------------------
# Write Analysis
# -------------------------------

if analysis_points:
    client.write_points(analysis_points)

print("\nAnalysis completed.")
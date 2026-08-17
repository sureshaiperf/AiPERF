from influxdb import DataFrameClient

client = DataFrameClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

query = """
SELECT *
FROM aiperf_execution_history
"""

result = client.query(query)

df = result["aiperf_execution_history"]

print(df)
print("\nTotal Executions:", len(df))
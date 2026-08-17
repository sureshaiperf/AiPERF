from influxdb import DataFrameClient

HOST = "localhost"
PORT = 8086
DATABASE = "jmeter"

client = DataFrameClient(
    host=HOST,
    port=PORT,
    database=DATABASE
)

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

print(df.head())
print("\nTotal Records:", len(df))
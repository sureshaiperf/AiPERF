from influxdb import InfluxDBClient

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

query = '''
SELECT LAST("p95") AS p95
FROM "aiperf_transaction_history"
GROUP BY "transaction"
'''

result = client.query(query)

points = []

for measurement, rows in result.items():

    transaction = measurement[1].get("transaction", "UNKNOWN")

    for row in rows:

        point = {
            "measurement": "aiperf_baseline",
            "tags": {
                "transaction": transaction
            },
            "fields": {
                "p95": float(row["p95"])
            }
        }

        points.append(point)

client.write_points(points)

print(f"{len(points)} baseline records saved.")
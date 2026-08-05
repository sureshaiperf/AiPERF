from influxdb import InfluxDBClient

print("Connecting to InfluxDB...")

client = InfluxDBClient(
    host='localhost',
    port=8086,
    database='jmeter'
)

query = '''
SELECT LAST("pct95.0") AS p95,
       LAST("count") AS total_count,
       LAST("countError") AS error_count
FROM "jmeter"
GROUP BY "transaction"
'''

result = client.query(query)

print("\n===== JMeter Metrics =====")

for measurement, points in result.items():

    transaction = measurement[1].get('transaction', 'UNKNOWN')

    for row in points:

        print(f"\nTransaction : {transaction}")
        print(f"P95 Response Time : {row.get('p95')}")
        print(f"Total Count       : {row.get('total_count')}")
        print(f"Error Count       : {row.get('error_count')}")

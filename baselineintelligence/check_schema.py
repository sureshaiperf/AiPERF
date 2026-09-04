from influxdb_client import InfluxDBClient

INFLUX_HOST = "localhost"
INFLUX_PORT = 8086
INFLUX_DB = "jmeter"

client = InfluxDBClient(
        host="localhost",
        port=8086,
        database="jmeter"
    )

query_api = client.query_api()

query = '''
import "influxdata/influxdb/schema"

schema.measurementFieldKeys(
  bucket: "aiperf",
  measurement: "aiperf_variance_ranking"
)
'''

tables = query_api.query(query)

print("\nFields in aiperf_variance_ranking:\n")

for table in tables:
    for record in table.records:
        print(record.get_value())

client.close()
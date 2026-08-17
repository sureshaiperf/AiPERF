from influxdb_client import InfluxDBClient
import pandas as pd

# -----------------------------
# InfluxDB Configuration
# -----------------------------
INFLUX_URL = "http://localhost:8086"
INFLUX_TOKEN = "YOUR_TOKEN"
INFLUX_ORG = "YOUR_ORG"
BUCKET = "jmeter"

client = InfluxDBClient(
    url=INFLUX_URL,
    token=INFLUX_TOKEN,
    org=INFLUX_ORG
)

query_api = client.query_api()

query = f'''
from(bucket: "{BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r["_measurement"] == "aiperf_execution_history")
'''

tables = query_api.query_data_frame(query)

print(tables.head())
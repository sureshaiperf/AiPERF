print("START")

from influxdb import InfluxDBClient

print("MODULE LOADED")

client = InfluxDBClient(
    host='localhost',
    port=8086,
    database='jmeter'
)

print("CONNECTED")
print(client.get_list_database())

print("END")
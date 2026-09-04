# check_measurements.py

from influxdb import InfluxDBClient

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

measurements = [
    "aiperf_transaction_comparison",
    "aiperf_service_comparison",
    "aiperf_variance_ranking",
    "aiperf_ai_insights",
    "aiperf_similar_execution"
]

for measurement in measurements:

    print("\n" + "=" * 80)
    print(measurement)
    print("=" * 80)

    result = client.query(
        f"SELECT * FROM {measurement} LIMIT 3"
    )

    points = list(result.get_points())

    for point in points:
        print(point)
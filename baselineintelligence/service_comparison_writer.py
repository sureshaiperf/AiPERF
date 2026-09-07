import os
from influxdb import InfluxDBClient

# =====================================================
# CONFIGURATION
# =====================================================

INFLUX_HOST = "localhost"
INFLUX_PORT = 8086
INFLUX_DB = "jmeter"

client = InfluxDBClient(
    host=INFLUX_HOST,
    port=INFLUX_PORT,
    database=INFLUX_DB
)

# =====================================================
# RUN ID
# =====================================================

current_run_id = os.getenv("RUN_ID")

if not current_run_id:
    raise Exception("RUN_ID environment variable not found")

print(f"Current Run : {current_run_id}")

# =====================================================
# GET COMPARISON RUN
# =====================================================

query = f"""
SELECT *
FROM aiperf_run_comparison
WHERE current_run_id='{current_run_id}'
ORDER BY time DESC
LIMIT 1
"""

rows = list(client.query(query).get_points())

if not rows:
    raise Exception(
        f"No run comparison found for {current_run_id}"
    )

comparison_row = rows[0]

similar_run_id = comparison_row["comparison_run_id"]

print(f"Current Run : {current_run_id}")
print(f"Compared Run: {similar_run_id}")

similarity_score = 0

# =====================================================
# GET SERVICE METRICS
# =====================================================

services = [
    "gateway",
    "user-service",
    "product-service",
    "order-service"
]

json_body = []

for service in services:

    current_query = f"""
    SELECT *
    FROM aiperf_service_metrics
    WHERE run_id='{current_run_id}'
    AND service_name='{service}'
    ORDER BY time DESC
    LIMIT 1
    """

    baseline_query = f"""
    SELECT *
    FROM aiperf_service_metrics
    WHERE run_id='{similar_run_id}'
    AND service_name='{service}'
    ORDER BY time DESC
    LIMIT 1
    """

    current_rows = list(
        client.query(current_query).get_points()
    )

    baseline_rows = list(
        client.query(baseline_query).get_points()
    )

    if not current_rows:
        continue

    if not baseline_rows:
        continue

    current = current_rows[0]
    baseline = baseline_rows[0]

    # ======================================
    # REQUEST COUNT
    # ======================================

    req_current = float(
        current.get("request_count", 0)
    )

    req_baseline = float(
        baseline.get("request_count", 0)
    )

    req_variance = 0.0
    request_count_comparable = int(
        current.get("request_count_available", 0)
        and baseline.get("request_count_available", 0)
    )

    if request_count_comparable and req_baseline > 0:

        req_variance = round(
            (
                (
                    req_current -
                    req_baseline
                )
                / req_baseline
            ) * 100,
            2
        )

    # ======================================
    # AVG RESPONSE TIME
    # ======================================

    rt_current = float(
        current.get(
            "avg_response_time_ms",
            0
        )
    )

    rt_baseline = float(
        baseline.get(
            "avg_response_time_ms",
            0
        )
    )

    rt_variance = 0.0

    if rt_baseline > 0:

        rt_variance = round(
            (
                (
                    rt_current -
                    rt_baseline
                )
                / rt_baseline
            ) * 100,
            2
        )

    # ======================================
    # HEAP %
    # ======================================

    heap_current = float(
        current.get(
            "heap_pct",
            0
        )
    )

    heap_baseline = float(
        baseline.get(
            "heap_pct",
            0
        )
    )

    heap_variance = 0.0

    if heap_baseline > 0:

        heap_variance = round(
            (
                (
                    heap_current -
                    heap_baseline
                )
                / heap_baseline
            ) * 100,
            2
        )

    # ======================================
    # GC OVERHEAD
    # ======================================

    gc_current = float(
        current.get(
            "gc_overhead",
            0
        )
    )

    gc_baseline = float(
        baseline.get(
            "gc_overhead",
            0
        )
    )

    gc_variance = 0.0

    if gc_baseline > 0:

        gc_variance = round(
            (
                (
                    gc_current -
                    gc_baseline
                )
                / gc_baseline
            ) * 100,
            2
        )

    print(
        f"{service} | "
        f"RT={rt_variance}% | "
        f"Heap={heap_variance}%"
    )

    json_body.append({

        "measurement":
            "aiperf_service_comparison",

        "tags": {

            "current_run_id":
                current_run_id,

            "similar_run_id":
                similar_run_id,

            "service_name":
                service
        },

        "fields": {

        "similarity_score":
            float(similarity_score),

        "request_count_baseline":
            float(req_baseline),

        "request_count_current":
            float(req_current),

        "request_count_variance_pct":
            float(req_variance),
        "request_count_comparable":
            request_count_comparable,

        "avg_rt_baseline":
            float(rt_baseline),

        "avg_rt_current":
            float(rt_current),

        "avg_rt_variance_pct":
            float(rt_variance),

        "heap_pct_baseline":
            float(heap_baseline),

        "heap_pct_current":
            float(heap_current),

        "heap_pct_variance_pct":
            float(heap_variance),

        "gc_baseline":
            float(gc_baseline),

        "gc_current":
            float(gc_current),

        "gc_variance_pct":
            float(gc_variance)
    }
    })

    print(
    f"Writing {len(json_body)} comparison records..."
)

# =====================================================
# WRITE
# =====================================================

if json_body:

    client.write_points(
    json_body,
    time_precision='s'
)

    print(
        "\nService comparison written successfully"
    )

else:

    print(
        "\nNo service comparison records generated"
    )
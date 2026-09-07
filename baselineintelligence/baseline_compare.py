from influxdb import InfluxDBClient
from datetime import datetime, timedelta
import statistics
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

RUN_ID = os.getenv("RUN_ID")
if not RUN_ID:
    raise RuntimeError("RUN_ID environment variable is required")
RUN_START_EPOCH = os.getenv("RUN_START_EPOCH")
RUN_END_EPOCH = os.getenv("RUN_END_EPOCH")
if not RUN_START_EPOCH or not RUN_END_EPOCH:
    raise RuntimeError(
        "RUN_START_EPOCH and RUN_END_EPOCH are required for run-scoped metrics"
    )

print("===== AiPERF Baseline Intelligence (Enhanced) =====")

try:
    client = InfluxDBClient(
        host='localhost',
        port=8086,
        database='jmeter'
    )
    client.ping()
except Exception as e:
    logger.error(f"Failed to connect to InfluxDB: {e}")
    exit(1)

# Transaction-specific thresholds for intelligent comparison
TRANSACTION_THRESHOLDS = {
    "default": {"warning": 15, "critical": 30},
    "login": {"warning": 10, "critical": 20},
    "checkout": {"warning": 20, "critical": 40},
    "search": {"warning": 25, "critical": 50},
}

def get_threshold(transaction_name):
    """Get appropriate thresholds based on transaction type"""
    for key, threshold in TRANSACTION_THRESHOLDS.items():
        if key.lower() in transaction_name.lower():
            return threshold
    return TRANSACTION_THRESHOLDS["default"]

def calculate_percentile(query_result, percentile_field):
    """Extract and aggregate percentile values"""
    values = []
    for measurement, points in query_result.items():
        for row in points:
            if percentile_field in row and row[percentile_field] is not None:
                values.append(float(row[percentile_field]))
    return values

def get_trend_analysis(transaction, days=7):
    """Analyze performance trend over specified days"""
    try:
        trend_query = f"""
        SELECT LAST("pct95.0") as p95
        FROM "jmeter"
        WHERE "transaction" = '{transaction}' 
        AND "statut" = 'all'
        AND time > now() - {days}d
        GROUP BY time(1d), "transaction"
        """
        result = client.query(trend_query)
        
        trend_values = []
        for measurement, points in result.items():
            for row in points:
                if row.get("p95") is not None:
                    trend_values.append(float(row["p95"]))
        
        if len(trend_values) > 1:
            trend = trend_values[-1] - trend_values[0]
            trend_pct = (trend / trend_values[0]) * 100 if trend_values[0] > 0 else 0
            return {
                "is_degrading": trend > 0,
                "trend_pct": round(trend_pct, 2),
                "data_points": len(trend_values)
            }
    except Exception as e:
        logger.warning(f"Could not analyze trend for {transaction}: {e}")
    
    return None

# Read Baseline with multiple percentiles
baseline_query = """
SELECT LAST("p95")
FROM "aiperf_baseline"
GROUP BY "transaction"
"""

try:
    baseline_result = client.query(baseline_query)
    baseline_map = {}
    
    for measurement, points in baseline_result.items():
        transaction = measurement[1].get("transaction")
        for row in points:
            baseline_map[transaction] = row["last"]
    logger.info(f"Loaded {len(baseline_map)} baseline records")
except Exception as e:
    logger.error(f"Failed to read baseline: {e}")
    exit(1)

# Read Current Metrics with enhanced data collection
current_query = f"""
SELECT 
    LAST("p50") as p50,
    LAST("p95") as p95,
    LAST("p99") as p99
FROM "aiperf_transaction_history"
WHERE time >= {int(RUN_START_EPOCH)}ms
AND time <= {int(RUN_END_EPOCH)}ms
GROUP BY "transaction"
"""

try:
    current_result = client.query(current_query)
except Exception as e:
    logger.error(f"Failed to read current metrics: {e}")
    exit(1)

analysis_points = []
summary_stats = {
    "total_transactions": 0,
    "passed": 0,
    "warnings": 0,
    "failed": 0,
    "new_transactions": 0
}

# Enhanced Comparison with Intelligence
for measurement, points in current_result.items():
    transaction = measurement[1].get("transaction")
    
    for row in points:
        summary_stats["total_transactions"] += 1
        
        current_p95 = row.get("p95")
        current_p50 = row.get("p50")
        current_p99 = row.get("p99")
        
        baseline_p95 = baseline_map.get(transaction)
        
        if baseline_p95 is None:
            summary_stats["new_transactions"] += 1
            logger.info(f"New transaction detected: {transaction}")
            status = "NEW"
            deviation = 0
            threshold = get_threshold(transaction)
        else:
            # Calculate deviation
            deviation = ((current_p95 - baseline_p95) / baseline_p95) * 100 if baseline_p95 > 0 else 0
            
            # Get transaction-specific threshold
            threshold = get_threshold(transaction)
            
            # Intelligent status determination
            if deviation <= threshold["warning"]:
                status = "PASS"
                summary_stats["passed"] += 1
            elif deviation <= threshold["critical"]:
                status = "WARNING"
                summary_stats["warnings"] += 1
            else:
                status = "FAIL"
                summary_stats["failed"] += 1
        
        # Trend analysis
        trend_info = get_trend_analysis(transaction)
        is_degrading = trend_info and trend_info["is_degrading"]
        trend_pct = trend_info.get("trend_pct", 0) if trend_info else 0
        
        # Build detailed output (defensive formatting for missing values)
        print("\n" + "=" * 50)
        print(f"Transaction : {transaction}")
        if baseline_p95 is not None:
            print(f"Baseline P95 : {baseline_p95:.2f} ms")
        else:
            print("Baseline P95 : N/A")

        if current_p95 is not None:
            print(f"Current P95  : {current_p95:.2f} ms")
        else:
            print("Current P95  : N/A")

        if current_p50 is not None:
            print(f"Current P50  : {current_p50:.2f} ms (median)")
        else:
            print("Current P50  : N/A")

        if current_p99 is not None:
            print(f"Current P99  : {current_p99:.2f} ms (tail latency)")
        else:
            print("Current P99  : N/A")

        print("Range        : N/A (run-scoped samples unavailable)")

        # Deviation and thresholds
        thr_warn = threshold.get('warning') if isinstance(threshold, dict) else TRANSACTION_THRESHOLDS['default']['warning']
        thr_crit = threshold.get('critical') if isinstance(threshold, dict) else TRANSACTION_THRESHOLDS['default']['critical']
        if baseline_p95 is not None:
            print(f"Deviation    : {deviation:.2f}% (threshold: {thr_warn}% / {thr_crit}%)")
        else:
            print(f"Deviation    : N/A (no baseline)")

        if trend_info:
            direction = (
                "DEGRADING" if trend_info["is_degrading"] else "IMPROVING"
            )
            print(f"Trend (7d)   : {direction} {abs(trend_pct):.2f}% ({trend_info['data_points']} samples)")

        print(f"Status       : {status}")
        
        # Store analysis with enriched data (only include available metrics)
        point_fields = {}
        if current_p50 is not None:
            point_fields["current_p50"] = round(current_p50, 2)
        if current_p95 is not None:
            point_fields["current_p95"] = round(current_p95, 2)
        if current_p99 is not None:
            point_fields["current_p99"] = round(current_p99, 2)

        point_fields["deviation"] = round(deviation, 2) if deviation is not None else None
        
        if baseline_p95 is not None:
            point_fields["baseline_p95"] = round(baseline_p95, 2)
        if trend_info:
            point_fields["trend_pct"] = trend_pct
            point_fields["is_degrading"] = int(is_degrading)
        
        analysis_point = {
            "measurement": "aiperf_analysis",
            "tags": {
                "run_id": RUN_ID,
                "transaction": transaction,
                "status": status,
                "degrading": "yes" if is_degrading else "no"
            },
            "fields": point_fields
        }
        
        analysis_points.append(analysis_point)

# Write Enhanced Analysis
if analysis_points:
    try:
        client.write_points(analysis_points)
        logger.info(f"Wrote {len(analysis_points)} analysis records to InfluxDB")
    except Exception as e:
        logger.error(f"Failed to write analysis: {e}")

# Print Summary Report
print("\n" + "=" * 50)
print("SUMMARY REPORT")
print("=" * 50)
print(f"Total Transactions  : {summary_stats['total_transactions']}")
print(f"Passed              : {summary_stats['passed']}")
print(f"Warnings            : {summary_stats['warnings']}")
print(f"Failed              : {summary_stats['failed']}")
print(f"New Transactions    : {summary_stats['new_transactions']}")

success_rate = (summary_stats['passed'] / summary_stats['total_transactions'] * 100) if summary_stats['total_transactions'] > 0 else 0
print(f"Success Rate        : {success_rate:.1f}%")

print("\nAnalysis completed at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
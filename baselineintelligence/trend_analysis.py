from influxdb import InfluxDBClient
from datetime import datetime
import math
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Connect to InfluxDB (adjust if needed)
client = InfluxDBClient(host='localhost', port=8086, database='jmeter')

# Query daily mean p95 for last 30 days grouped by transaction
query = '''
SELECT MEAN("pct95.0") as p95
FROM "jmeter"
WHERE time > now() - 30d
GROUP BY time(1d), "transaction" fill(none)
'''

result = client.query(query)

# Organize data per transaction
from collections import defaultdict
transaction_series = defaultdict(list)

for measurement, points in result.items():
    tags = measurement[1]
    transaction = tags.get('transaction', 'Unknown')
    for row in points:
        t = row.get('time')
        p95 = row.get('p95')
        if p95 is None:
            continue
        # parse time to epoch seconds
        try:
            dt = datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            dt = datetime.fromisoformat(t.replace('Z', '+00:00'))
        epoch = dt.timestamp()
        transaction_series[transaction].append((epoch, float(p95)))


# Simple linear regression and trend metrics
def linear_regression(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean)*(ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean)**2 for i in range(n))
    if den == 0:
        return None
    slope = num / den
    intercept = y_mean - slope * x_mean
    # r-squared
    ss_tot = sum((yi - y_mean)**2 for yi in ys)
    ss_res = sum((ys[i] - (slope*xs[i] + intercept))**2 for i in range(n))
    r2 = 1 - ss_res/ss_tot if ss_tot != 0 else 0
    return slope, intercept, r2

analysis_points = []
print('\n===== Trend Analysis (last 30 days) =====\n')

for transaction, series in transaction_series.items():
    series.sort()
    xs = [s[0] for s in series]
    ys = [s[1] for s in series]
    if len(xs) < 2:
        logger.info(f"Skipping {transaction}: insufficient data points ({len(xs)})")
        continue
    # normalize time to days since start for interpretability
    start = xs[0]
    x_days = [ (x - start) / 86400.0 for x in xs]
    reg = linear_regression(x_days, ys)
    if reg is None:
        logger.info(f"Skipping {transaction}: regression failed")
        continue
    slope, intercept, r2 = reg
    # compute trend over period
    first = ys[0]
    last = ys[-1]
    trend_abs = last - first
    trend_pct = (trend_abs / first) * 100 if first != 0 else 0
    # severity thresholds
    if trend_abs <= 0:
        severity = 'STABLE'
    elif trend_pct <= 15:
        severity = 'WARNING'
    else:
        severity = 'CRITICAL'

    degrading = 1 if trend_abs > 0 else 0

    print('Transaction:', transaction)
    direction = 'DEGRADING' if degrading else 'IMPROVING'
    print(f'  Samples: {len(xs)}, R^2: {r2:.3f}, slope/day: {slope:.3f}, trend%: {trend_pct:.2f}, direction: {direction}, severity: {severity}')

    analysis_points.append({
        'measurement': 'aiperf_trend_analysis',
        'tags': {
            'run_id': os.getenv('RUN_ID', 'historical'),
            'transaction': transaction,
            'severity': severity,
            'direction': direction
        },
        'fields': {
            'slope_per_day': float(round(slope, 6)),
            'r2': float(round(r2, 4)),
            'trend_pct': float(round(trend_pct, 3)),
            'degrading': int(degrading),
            'samples': int(len(xs))
        }
    })

# write analysis points
if analysis_points:
    client.write_points(analysis_points)
    print('\nWrote', len(analysis_points), 'trend analysis records to InfluxDB')
else:
    print('\nNo analysis points to write')

print('\nTrend analysis completed.')

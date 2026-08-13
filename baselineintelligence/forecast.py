from datetime import datetime, timedelta
import logging
import math
import statistics

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Lazy InfluxDB client creation to allow importing without the package during tests

def get_influx_client(host=None, port=None, database=None):
    try:
        from influxdb import InfluxDBClient
    except ImportError:
        logger.error('influxdb package is required to run forecasting against InfluxDB')
        raise
    host = host or 'localhost'
    port = int(port or 8086)
    database = database or 'jmeter'
    return InfluxDBClient(host=host, port=port, database=database)


def ols_regression(x, y):
    n = len(x)
    if n < 2:
        return None
    x_mean = sum(x) / n
    y_mean = sum(y) / n
    num = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
    den = sum((x[i] - x_mean) ** 2 for i in range(n))
    if den == 0:
        return None
    slope = num / den
    intercept = y_mean - slope * x_mean
    # r2
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    ss_res = sum((y[i] - (slope * x[i] + intercept)) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot != 0 else 0
    # residual std error
    sigma2 = ss_res / (n - 2) if n > 2 else 0
    sigma = math.sqrt(sigma2) if sigma2 > 0 else 0
    return {
        'slope': slope,
        'intercept': intercept,
        'r2': r2,
        'sigma': sigma,
        'n': n,
        'x_mean': x_mean,
        'den': den
    }


def predict_and_interval(model, x0, n, alpha=0.05):
    # x0 is scalar (days since start). Return prediction and 95% PI using t approx
    slope = model['slope']
    intercept = model['intercept']
    sigma = model['sigma']
    x_mean = model['x_mean']
    den = model['den']
    y_pred = slope * x0 + intercept
    if model['n'] <= 2 or sigma == 0:
        # no interval info
        return y_pred, y_pred, y_pred
    # standard error of prediction
    se = math.sqrt(sigma ** 2 * (1.0 + 1.0 / n + ((x0 - x_mean) ** 2) / den))
    # t critical ~ for large n use 1.96; for small n, we could use scipy but avoid dependency
    t_crit = 1.96 if n > 30 else 2.0
    lower = y_pred - t_crit * se
    upper = y_pred + t_crit * se
    return y_pred, lower, upper


def run_forecast(days_history=90, horizon=7):
    client = get_influx_client()

    # Query daily aggregates for relevant metrics
    query = f"""
SELECT
  MEAN("mean") AS avg_rt,
  MEAN("pct90.0") AS p90,
  MEAN("pct95.0") AS p95,
  MEAN("pct99.0") AS p99,
  SUM("count") AS total_count,
  SUM("countError") AS error_count
FROM "jmeter"
WHERE time > now() - {days_history}d
GROUP BY time(1d), "transaction" fill(none)
"""

    result = client.query(query)

    from collections import defaultdict
    series = defaultdict(list)

    for measurement, points in result.items():
        tags = measurement[1]
        transaction = tags.get('transaction', 'UNKNOWN')
        for row in points:
            t = row.get('time')
            try:
                dt = datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                dt = datetime.fromisoformat(t.replace('Z', '+00:00'))
            epoch = dt.timestamp()
            avg = row.get('avg_rt')
            p90 = row.get('p90')
            p95 = row.get('p95')
            p99 = row.get('p99')
            total = row.get('total_count')
            err = row.get('error_count')
            error_pct = (float(err) / float(total) * 100.0) if total and total != 0 and err is not None else None
            series[transaction].append({'time': dt, 'epoch': epoch, 'avg': avg, 'p90': p90, 'p95': p95, 'p99': p99, 'error_pct': error_pct})

    # For each transaction and metric, compute regression and forecasts
    forecast_points = []
    alerts = []

    for transaction, rows in series.items():
        rows.sort(key=lambda r: r['time'])
        if len(rows) < 5:
            logger.info(f"Skipping {transaction}: insufficient data ({len(rows)})")
            continue
        # x as days since start
        start = rows[0]['epoch']
        xs = [ (r['epoch'] - start) / 86400.0 for r in rows ]
        metrics = ['avg', 'p90', 'p95', 'p99', 'error_pct']
        for metric in metrics:
            ys = [ r[metric] for r in rows if r[metric] is not None ]
            # Align xs with ys - rebuild aligned pairs
            aligned = [ (xs[i], rows[i][metric]) for i in range(len(rows)) if rows[i][metric] is not None ]
            if len(aligned) < 5:
                continue
            x_aligned = [ p[0] for p in aligned ]
            y_aligned = [ float(p[1]) for p in aligned ]
            model = ols_regression(x_aligned, y_aligned)
            if model is None:
                continue
            n = model['n']
            last_x = x_aligned[-1]
            last_actual = y_aligned[-1]
            # compute future predictions
            for h in range(1, horizon+1):
                x0 = last_x + h
                y_pred, lower, upper = predict_and_interval(model, x0, n)
                # build timestamp for the day (use last row time + h days at midnight)
                pred_time = rows[-1]['time'] + timedelta(days=h)
                point = {
                    'measurement': 'aiperf_forecast',
                    'tags': {'transaction': transaction, 'metric': metric},
                    'time': pred_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'fields': {
                        'predicted': float(round(y_pred, 4)),
                        'lower_95': float(round(lower, 4)),
                        'upper_95': float(round(upper, 4)),
                        'horizon_days': int(h),
                        'r2': float(round(model['r2'], 4)),
                        'slope_per_day': float(round(model['slope'], 6)),
                        'samples': int(model['n'])
                    }
                }
                forecast_points.append(point)
            # check latest actual against last prediction interval for this metric (h=0 predicted at last_x)
            # compute point estimate at last_x
            y_hat_last = model['slope'] * last_x + model['intercept']
            # compute interval for last_x
            _, lower0, upper0 = predict_and_interval(model, last_x, n)
            # if actual outside upper/lower, create alert
            if last_actual is not None and (last_actual > upper0 or last_actual < lower0):
                severity = 'HIGH' if model['r2'] > 0.5 else 'MEDIUM'
                alerts.append({
                    'measurement': 'aiperf_alerts',
                    'tags': {'transaction': transaction, 'metric': metric, 'severity': severity},
                    'fields': {
                        'actual': float(round(last_actual,4)),
                        'predicted': float(round(y_hat_last,4)),
                        'lower_95': float(round(lower0,4)),
                        'upper_95': float(round(upper0,4)),
                        'r2': float(round(model['r2'],4))
                    }
                })

    # write forecasts and alerts to InfluxDB
    if forecast_points:
        client.write_points(forecast_points)
        logger.info(f"Wrote {len(forecast_points)} forecast points to InfluxDB (aiperf_forecast)")
    else:
        logger.info("No forecast points to write")

    if alerts:
        client.write_points(alerts)
        logger.info(f"Wrote {len(alerts)} alerts to InfluxDB (aiperf_alerts)")
    else:
        logger.info("No alerts generated")

    print('Forecast generation completed')


if __name__ == '__main__':
    run_forecast()

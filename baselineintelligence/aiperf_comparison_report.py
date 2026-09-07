"""Generate compact transaction and server comparison HTML reports."""

from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections import defaultdict

from influxdb import InfluxDBClient


def _value(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        return "N/A"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _variance_class(row: dict[str, Any]) -> str:
    try:
        variance = float(row.get("variance_pct", 0))
    except (TypeError, ValueError):
        return ""
    return "bad" if variance > 15 else "warn" if variance > 5 else "good"


def _variance(value: Any) -> str:
    try:
        return f"{float(value):,.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _variance_span(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '<span style="color:#64748b">N/A</span>'
    css = "bad" if number > 15 else "warn" if number > 5 else "good"
    color = {"bad": "#b91c1c", "warn": "#b45309", "good": "#15803d"}[css]
    weight = "700" if css == "bad" else "600" if css == "warn" else "400"
    return f'<span style="color:{color};font-weight:{weight}">{number:,.2f}%</span>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(
        f'<th style="background:#1e3a5f;color:white;text-align:left;'
        f'padding:8px 10px;border:1px solid #cbd5e1;white-space:nowrap">'
        f"{html.escape(item)}</th>"
        for item in headers
    )
    body = "".join(
        '<tr style="border:1px solid #cbd5e1">'
        + "".join(
            f'<td style="padding:8px 10px;border:1px solid #cbd5e1;'
            f'white-space:nowrap">{cell}</td>'
            for cell in row
        )
        + "</tr>\n"
        for row in rows
    )
    return (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%;font:13px Segoe UI,Arial,sans-serif">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _query_rows(client: Any, measurement: str, run_id: str) -> list[dict[str, Any]]:
    query = (
        f'SELECT * FROM "{measurement}" '
        f"WHERE current_run_id='{run_id}' ORDER BY time DESC"
    )
    return list(client.query(query).get_points())


def _query_execution(client: Any, run_id: str) -> dict[str, Any]:
    if not run_id or run_id == "Unavailable":
        return {}
    query = (
        'SELECT * FROM "aiperf_execution_history" '
        f"WHERE run_id='{run_id}' ORDER BY time DESC LIMIT 1"
    )
    rows = list(client.query(query).get_points())
    return rows[0] if rows else {}


def _epoch_ms(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number * 1000) if number < 10_000_000_000 else int(number)


def _format_time(value: Any) -> str:
    epoch_ms = _epoch_ms(value)
    if epoch_ms is None:
        return "Unavailable"
    return datetime.fromtimestamp(
        epoch_ms / 1000,
        tz=timezone.utc,
    ).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _timeline(client: Any, run_id: str, current: bool = False) -> dict[str, str]:
    execution = _query_execution(client, run_id)
    start = execution.get("run_start_epoch")
    end = execution.get("run_end_epoch")
    if current:
        start = start or os.getenv("RUN_START_EPOCH")
        end = end or os.getenv("RUN_END_EPOCH")
    duration = execution.get("duration_seconds")
    start_ms = _epoch_ms(start)
    end_ms = _epoch_ms(end)
    if duration is None and start_ms is not None and end_ms is not None:
        duration = round((end_ms - start_ms) / 1000, 3)
    return {
        "start": _format_time(start),
        "end": _format_time(end),
        "duration": f"{float(duration):,.3f} s"
        if duration is not None
        else "Unavailable",
    }


def _comparison_run_id(
    transactions: list[dict[str, Any]],
    services: list[dict[str, Any]],
) -> str:
    for row in transactions + services:
        if row.get("comparison_run_id"):
            return str(row["comparison_run_id"])
    return "Unavailable"


def build_report(
    client: Any,
    run_id: str,
    output_dir: str | os.PathLike[str],
) -> Path:
    transactions = _query_rows(client, "aiperf_transaction_comparison", run_id)
    services = _query_rows(client, "aiperf_service_comparison", run_id)
    comparison_run_id = _comparison_run_id(transactions, services)
    current_timeline = _timeline(client, run_id, current=True)
    comparison_timeline = _timeline(client, comparison_run_id)

    transaction_metrics: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in transactions:
        name = str(row.get("transaction", row.get("entity_name", "N/A")))
        transaction_metrics[name][str(row.get("metric", "N/A"))] = row

    transaction_rows = []
    for transaction, metrics in sorted(transaction_metrics.items()):
        transaction_rows.append(
            [
                html.escape(transaction),
                _value(metrics.get("avg_rt", {}), "previous_value"),
                _value(metrics.get("avg_rt", {}), "current_value"),
                _variance_span(metrics.get("avg_rt", {}).get("variance_pct")),
                _value(metrics.get("p95", {}), "previous_value"),
                _value(metrics.get("p95", {}), "current_value"),
                _variance_span(metrics.get("p95", {}).get("variance_pct")),
                _value(metrics.get("p99", {}), "previous_value"),
                _value(metrics.get("p99", {}), "current_value"),
                _variance_span(metrics.get("p99", {}).get("variance_pct")),
                _value(metrics.get("error_pct", {}), "current_value") + "%",
            ]
        )

    service_rows = []
    for row in services:
        service_rows.append(
            [
                html.escape(str(row.get("service_name", "N/A"))),
                _value(row, "avg_rt_baseline"),
                _value(row, "avg_rt_current"),
                f'<span class="{_variance_class({"variance_pct": row.get("avg_rt_variance_pct")})}">'
                f'{_value(row, "avg_rt_variance_pct")}%</span>',
                _value(row, "heap_pct_baseline"),
                _value(row, "heap_pct_current"),
                _variance_span(row.get("heap_pct_variance_pct")),
                _value(row, "active_requests_current"),
                _value(row, "executor_active_current"),
                _value(row, "gc_current"),
                "Comparable" if row.get("request_count_comparable") else "Unavailable",
            ]
        )

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "index.html"
    report_path.write_text(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><title>AiPERF Comparison Report</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #1f2937; }}
h1 {{ margin-bottom: 4px; }} .meta {{ color: #64748b; margin-bottom: 20px; }}
.tabs {{ display: flex; gap: 8px; margin-bottom: 12px; }}
button {{ border: 0; padding: 10px 16px; cursor: pointer; background: #dbeafe; }}
button.active {{ background: #2563eb; color: white; }}
.panel {{ display: none; }} .panel.active {{ display: block; }}
table {{ border-collapse: separate; border-spacing: 0; width: 100%; font-size: 13px; margin-top: 12px; }}
th {{ background: #1e3a5f; color: white; text-align: left; white-space: nowrap; }}
th, td {{ border-right: 1px solid #cbd5e1; border-bottom: 1px solid #cbd5e1; padding: 9px 10px; white-space: nowrap; }}
th:first-child, td:first-child {{ border-left: 1px solid #cbd5e1; }}
thead tr:first-child th {{ border-top: 1px solid #cbd5e1; }}
th[colspan] {{ text-align: center; background: #334e68; }}
td:first-child {{ font-weight: 600; }}
tr:nth-child(even) {{ background: #f8fafc; }}
.good {{ color: #15803d; }} .warn {{ color: #b45309; font-weight: 600; }}
.bad {{ color: #b91c1c; font-weight: 700; }}
.na {{ color: #64748b; }}
.table-wrap {{ overflow-x: auto; }}
.run-summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; margin: 16px 0 22px; }}
.run-card {{ border: 1px solid #cbd5e1; border-radius: 6px; padding: 12px 14px; background: #f8fafc; }}
.run-card h3 {{ margin: 0 0 8px; color: #1e3a5f; }}
.run-card p {{ margin: 4px 0; }}
.notice {{ border-left: 4px solid #b45309; padding: 10px 12px; background: #fff7ed; color: #7c2d12; margin: 12px 0 20px; }}
</style></head><body>
<h1>AiPERF Comparison Report</h1>
<div class="meta">Generated from InfluxDB comparison measurements</div>
<div class="run-summary" style="display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 22px">
  <div class="run-card" style="border:1px solid #cbd5e1;border-radius:6px;padding:12px 14px;background:#f8fafc;min-width:280px"><h3>Current Test Run</h3>
    <p><strong>Run ID:</strong> {html.escape(run_id)}</p>
    <p><strong>Start:</strong> {html.escape(current_timeline["start"])}</p>
    <p><strong>End:</strong> {html.escape(current_timeline["end"])}</p>
    <p><strong>Duration:</strong> {html.escape(current_timeline["duration"])}</p>
  </div>
  <div class="run-card" style="border:1px solid #cbd5e1;border-radius:6px;padding:12px 14px;background:#f8fafc;min-width:280px"><h3>Compared Reference Run</h3>
    <p><strong>Run ID:</strong> {html.escape(comparison_run_id)}</p>
    <p><strong>Start:</strong> {html.escape(comparison_timeline["start"])}</p>
    <p><strong>End:</strong> {html.escape(comparison_timeline["end"])}</p>
    <p><strong>Duration:</strong> {html.escape(comparison_timeline["duration"])}</p>
  </div>
</div>
<div class="notice" style="border-left:4px solid #b45309;padding:10px 12px;background:#fff7ed;color:#7c2d12;margin:12px 0 20px"><strong>Baseline discussion:</strong> This report compares the current run with the reference run shown above. The reference run is not yet a statistically stable multi-run baseline; review and approve the baseline separately before using it for release thresholds.</div>
<div class="tabs">
<button class="tab active" data-panel="transactions">Transaction Comparison</button>
<button class="tab" data-panel="services">Server Metrics Comparison</button>
</div>
<section id="transactions" class="panel active">
<h2>Transaction Comparison</h2>
<div class="meta">Latency values are milliseconds; error rate is percent.</div>
<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font:13px Segoe UI,Arial,sans-serif">
<thead><tr><th rowspan="2" style="background:#1e3a5f;color:white;padding:8px 10px;border:1px solid #cbd5e1">Transaction</th>
<th colspan="3" style="background:#334e68;color:white;text-align:center;padding:8px 10px;border:1px solid #cbd5e1">Average Response Time (ms)</th>
<th colspan="3" style="background:#334e68;color:white;text-align:center;padding:8px 10px;border:1px solid #cbd5e1">P95 (ms)</th>
<th colspan="3" style="background:#334e68;color:white;text-align:center;padding:8px 10px;border:1px solid #cbd5e1">P99 (ms)</th>
<th rowspan="2" style="background:#1e3a5f;color:white;padding:8px 10px;border:1px solid #cbd5e1">Current Error Rate</th></tr>
<tr>{"".join(f'<th style="background:#1e3a5f;color:white;padding:8px 10px;border:1px solid #cbd5e1">{label}</th>' for label in ("Reference", "Current", "Variance") * 3)}</tr></thead>
<tbody>{"".join('<tr>' + ''.join(f'<td style="padding:8px 10px;border:1px solid #cbd5e1;white-space:nowrap">{cell}</td>' for cell in row) + "</tr>\n" for row in transaction_rows)}</tbody>
</table></div>
</section>
<section id="services" class="panel">
<h2>Server Metrics Comparison</h2>
<div class="meta">Response time is milliseconds; heap is percent; active/executor/GC are current snapshots. Request count is a run delta. Reference values are from the compared run above, not a statistical baseline.</div>
<div class="table-wrap">{_table(["Service", "Avg RT Reference", "Avg RT Current", "Avg RT Variance",
        "Heap Reference", "Heap Current", "Heap Variance", "Active Requests",
        "Executor Active", "GC Overhead", "Request Count"], service_rows)}</div>
</section>
<script>
document.querySelectorAll('.tab').forEach(function(button) {{
  button.onclick = function() {{
    document.querySelectorAll('.tab,.panel').forEach(function(item) {{ item.classList.remove('active'); }});
    button.classList.add('active');
    document.getElementById(button.dataset.panel).classList.add('active');
  }};
}});
</script></body></html>""",
        encoding="utf-8",
    )
    return report_path


def main() -> int:
    run_id = os.getenv("RUN_ID")
    if not run_id:
        raise RuntimeError("RUN_ID environment variable is required")
    client = InfluxDBClient(
        host=os.getenv("INFLUX_HOST", "localhost"),
        port=int(os.getenv("INFLUX_PORT", "8086")),
        database=os.getenv("INFLUX_DATABASE", "jmeter"),
    )
    report_path = build_report(
        client,
        run_id,
        os.getenv("AIPERF_REPORT_DIR", r"C:\practice\AiPERF\reports"),
    )
    print(f"Comparison report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate compact transaction and server comparison HTML reports."""

from __future__ import annotations

import html
import os
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
        return '<span class="na">N/A</span>'
    css = "bad" if number > 15 else "warn" if number > 5 else "good"
    return f'<span class="{css}">{number:,.2f}%</span>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>\n"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _query_rows(client: Any, measurement: str, run_id: str) -> list[dict[str, Any]]:
    query = (
        f'SELECT * FROM "{measurement}" '
        f"WHERE current_run_id='{run_id}' ORDER BY time DESC"
    )
    return list(client.query(query).get_points())


def build_report(
    client: Any,
    run_id: str,
    output_dir: str | os.PathLike[str],
) -> Path:
    transactions = _query_rows(client, "aiperf_transaction_comparison", run_id)
    services = _query_rows(client, "aiperf_service_comparison", run_id)

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
</style></head><body>
<h1>AiPERF Comparison Report</h1>
<div class="meta">RUN_ID: {html.escape(run_id)} | Generated from InfluxDB comparison measurements</div>
<div class="tabs">
<button class="tab active" data-panel="transactions">Transaction Comparison</button>
<button class="tab" data-panel="services">Server Metrics Comparison</button>
</div>
<section id="transactions" class="panel active">
<h2>Transaction Comparison</h2>
<div class="meta">Latency values are milliseconds; error rate is percent.</div>
<div class="table-wrap"><table>
<thead><tr><th rowspan="2">Transaction</th>
<th colspan="3">Average Response Time (ms)</th>
<th colspan="3">P95 (ms)</th>
<th colspan="3">P99 (ms)</th>
<th rowspan="2">Current Error Rate</th></tr>
<tr><th>Baseline</th><th>Current</th><th>Variance</th>
<th>Baseline</th><th>Current</th><th>Variance</th>
<th>Baseline</th><th>Current</th><th>Variance</th></tr></thead>
<tbody>{"".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>\n" for row in transaction_rows)}</tbody>
</table></div>
</section>
<section id="services" class="panel">
<h2>Server Metrics Comparison</h2>
<div class="meta">Response time is milliseconds; heap is percent; active/executor/GC are current snapshots. Request count is a run delta.</div>
<div class="table-wrap">{_table(["Service", "Avg RT Baseline", "Avg RT Current", "Avg RT Variance",
         "Heap Baseline", "Heap Current", "Heap Variance", "Active Requests",
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

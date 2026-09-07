"""Generate compact transaction and server comparison HTML reports."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any

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


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
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

    transaction_rows = []
    for row in transactions:
        variance = _value(row, "variance_pct")
        transaction_rows.append(
            [
                html.escape(str(row.get("transaction", row.get("entity_name", "N/A")))),
                html.escape(str(row.get("metric", "N/A"))),
                _value(row, "previous_value"),
                _value(row, "current_value"),
                f'<span class="{_variance_class(row)}">{variance}%</span>',
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
                _value(row, "heap_pct_variance_pct") + "%",
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
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th {{ background: #1e3a5f; color: white; text-align: left; }}
th, td {{ border: 1px solid #cbd5e1; padding: 7px 9px; }}
tr:nth-child(even) {{ background: #f8fafc; }}
.good {{ color: #15803d; }} .warn {{ color: #b45309; font-weight: 600; }}
.bad {{ color: #b91c1c; font-weight: 700; }}
</style></head><body>
<h1>AiPERF Comparison Report</h1>
<div class="meta">RUN_ID: {html.escape(run_id)} | Generated from InfluxDB comparison measurements</div>
<div class="tabs">
<button class="tab active" data-panel="transactions">Transaction Comparison</button>
<button class="tab" data-panel="services">Server Metrics Comparison</button>
</div>
<section id="transactions" class="panel active">
<h2>Transaction Comparison</h2>
{_table(["Transaction", "Metric", "Baseline", "Current", "Variance"], transaction_rows)}
</section>
<section id="services" class="panel">
<h2>Server Metrics Comparison</h2>
{_table(["Service", "Avg RT Baseline", "Avg RT Current", "Avg RT Variance",
         "Heap Baseline", "Heap Current", "Heap Variance", "Request Count"], service_rows)}
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

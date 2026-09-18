"""Register and govern an AiPERF approved performance baseline in InfluxDB 1.8."""
from __future__ import annotations
import argparse, json, os, re
from datetime import UTC, datetime
from typing import Any
from influxdb import InfluxDBClient
from baseline_selection import REGISTRY_MEASUREMENT, latest_execution, safe_run_id

def csv_ids(value: str) -> list[str]:
    return [safe_run_id(x.strip()) for x in value.split(",") if x.strip()]

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-id", required=True)
    p.add_argument("--representative-run-id", required=True)
    p.add_argument("--source-run-ids", required=True)
    p.add_argument("--excluded-run-ids", default="")
    p.add_argument("--approved-by", required=True)
    p.add_argument("--approval-reason", required=True)
    p.add_argument("--status", choices=("CANDIDATE","APPROVED","RETIRED","REJECTED"), default="APPROVED")
    a=p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", a.baseline_id): raise ValueError("Invalid baseline ID")
    representative=safe_run_id(a.representative_run_id); sources=csv_ids(a.source_run_ids); excluded=csv_ids(a.excluded_run_ids)
    if representative not in sources: raise ValueError("Representative run must be included in source runs")
    client=InfluxDBClient(host=os.getenv("INFLUX_HOST","localhost"),port=int(os.getenv("INFLUX_PORT","8086")),username=os.getenv("INFLUX_USER") or None,password=os.getenv("INFLUX_PASSWORD") or None,database=os.getenv("INFLUX_DATABASE",os.getenv("INFLUX_DB","jmeter")),timeout=int(os.getenv("INFLUX_TIMEOUT_SECONDS","30")))
    try:
        client.ping(); rows=[]
        for run_id in sources:
            row=latest_execution(client,run_id)
            if not row: raise RuntimeError(f"Source execution unavailable: {run_id}")
            rows.append(row)
        rep=latest_execution(client,representative)
        semantic=int(float(rep.get("throughput_semantic_version",2)))
        metrics={k: sorted(float(r.get(k,0) or 0) for r in rows)[len(rows)//2] for k in ("avg_rt","p95","p99","throughput","sample_count","test_duration_seconds","error_rate")}
        point={"measurement":REGISTRY_MEASUREMENT,"tags":{"baseline_id":a.baseline_id,"status":a.status,"representative_run_id":representative,"application":str(rep.get("application","")),"environment":str(rep.get("environment","")),"test_name":str(rep.get("test_name","")),"semantic_version":str(semantic)},"fields":{"profile_type":"FIVE_RUN_MEDIAN","source_run_ids_json":json.dumps(sources),"excluded_run_ids_json":json.dumps(excluded),"aggregate_metrics_json":json.dumps(metrics,sort_keys=True),"approved_by":a.approved_by.strip(),"approved_at":datetime.now(UTC).isoformat(),"approval_reason":a.approval_reason.strip(),"schema_version":"aiperf-baseline.v1"}}
        if client.write_points([point]) is False: raise RuntimeError("InfluxDB rejected baseline registry")
        print(json.dumps({"baseline_id":a.baseline_id,"status":a.status,"representative_run_id":representative,"semantic_version":semantic,"aggregate_metrics":metrics},indent=2))
        return 0
    finally: client.close()
if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"Baseline Registry Failed: {type(exc).__name__}: {exc}"); raise SystemExit(1)

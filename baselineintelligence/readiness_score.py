"""Calculate direction-aware AiPERF release readiness for one run."""
from __future__ import annotations
import argparse, math, os, re
from typing import Any
from influxdb import InfluxDBClient

def safe(value: Any) -> str:
    text=str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+",text): raise ValueError("Invalid RUN_ID")
    return text

def number(value: Any) -> float:
    try:
        x=float(value); return x if math.isfinite(x) else 0.0
    except (TypeError,ValueError,OverflowError): return 0.0

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--run-id",default=os.getenv("RUN_ID")); a=p.parse_args(); run_id=safe(a.run_id)
    client=InfluxDBClient(host=os.getenv("INFLUX_HOST","localhost"),port=int(os.getenv("INFLUX_PORT","8086")),username=os.getenv("INFLUX_USER") or None,password=os.getenv("INFLUX_PASSWORD") or None,database=os.getenv("INFLUX_DATABASE",os.getenv("INFLUX_DB","jmeter")),timeout=int(os.getenv("INFLUX_TIMEOUT_SECONDS","30")))
    try:
        client.ping()
        tx=list(client.query(f'SELECT * FROM "aiperf_transaction_comparison" WHERE "current_run_id"=\'{run_id}\'').get_points())
        anomalies=list(client.query(f'SELECT * FROM "aiperf_anomaly_detection" WHERE "run_id"=\'{run_id}\'').get_points())
        regressions=[row for row in tx if number(row.get("variance_pct"))>0 and str(row.get("metric","")) in {"avg_rt","p95","p99","error_pct"}]
        warning_anomalies=[row for row in anomalies if str(row.get("direction","")).lower()=="degradation" and str(row.get("severity","")).upper()=="WARNING" and row.get("is_anomaly") in (1,True,"1","true","True")]
        critical_anomalies=[row for row in anomalies if str(row.get("direction","")).lower()=="degradation" and str(row.get("severity","")).upper()=="CRITICAL" and row.get("is_anomaly") in (1,True,"1","true","True")]
        max_reg=max((number(x.get("variance_pct")) for x in regressions),default=0.0)
        score=100; fail_count=0; warning_count=0
        if critical_anomalies: fail_count+=len(critical_anomalies); score-=min(60,30*len(critical_anomalies))
        if warning_anomalies: warning_count+=len(warning_anomalies); score-=min(20,10*len(warning_anomalies))
        if max_reg>=30: fail_count+=1; score-=25
        elif max_reg>=15: warning_count+=1; score-=10
        score=max(0,score); readiness="PRODUCTION READY" if score>=90 else "READY WITH OBSERVATIONS" if score>=75 else "HIGH RISK" if score>=60 else "NOT READY"
        print("\n===== AiPERF Release Readiness =====\n"); print(f"Actionable transaction regressions : {len(regressions)}"); print(f"Degrading warning anomalies        : {len(warning_anomalies)}"); print(f"Degrading critical anomalies       : {len(critical_anomalies)}"); print(f"Release Score : {score}/100"); print(f"Status        : {readiness}")
        client.query(f'DROP SERIES FROM "aiperf_release_readiness" WHERE "run_id"=\'{run_id}\'')
        point={"measurement":"aiperf_release_readiness","tags":{"application":"AiPERF","run_id":run_id},"fields":{"release_score":int(score),"fail_count":int(fail_count),"warning_count":int(warning_count),"status":readiness,"max_actionable_regression_pct":float(max_reg),"direction_aware":1,"schema_version":"aiperf-readiness.v2"}}
        if client.write_points([point]) is False: raise RuntimeError("InfluxDB rejected readiness result")
        print("\nStored in InfluxDB"); print(f"Run ID : {run_id}"); return 0
    finally: client.close()
if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"Readiness calculation failed: {type(exc).__name__}: {exc}"); raise SystemExit(1)

"""Direction-aware bottleneck intelligence with InfluxDB v1 persistence."""
from __future__ import annotations
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any
RESOURCE_PRESSURE_THRESHOLD_PCT = 80.0

def _n(value: Any) -> float:
    try:
        number=float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError, OverflowError): return 0.0

def _text(value: Any) -> str: return "" if value is None else str(value).strip()
def _metric(row: Mapping[str, Any]) -> str: return re.sub(r"[^a-z0-9]+","_",_text(row.get("metric")).lower()).strip("_")

def _signals(row: Mapping[str, Any]) -> tuple[float,float,float]:
    metric=_metric(row)
    variance=_n(row.get("variance_pct", row.get("avg_rt_variance_pct")))
    latency=variance if variance > 0 and metric not in {"heap_pct","cpu_pct","system_cpu","process_cpu","gc","gc_overhead"} else 0.0
    if row.get("avg_rt_variance_pct") is not None:
        latency=max(0.0,_n(row.get("avg_rt_variance_pct")))
    errors=max(0.0,_n(row.get("error_rate",row.get("error_pct"))))
    absolute_resource=max(_n(row.get("cpu_pct",row.get("process_cpu"))),_n(row.get("heap_pct_current",row.get("heap_pct"))),_n(row.get("gc_current",row.get("gc_overhead"))))
    resource=max(0.0,absolute_resource-RESOURCE_PRESSURE_THRESHOLD_PCT) if absolute_resource >= RESOURCE_PRESSURE_THRESHOLD_PCT else 0.0
    return latency,errors,resource

def analyze_bottlenecks(rows: Iterable[Mapping[str, Any]], *, run_id: str, limit: int=10) -> dict[str, Any]:
    ranked=[]
    for raw in rows:
        row=dict(raw)
        if _text(row.get("run_id",row.get("current_run_id"))) != _text(run_id): continue
        latency,errors,resource=_signals(row)
        score=latency+errors*2+resource*.5
        if score <= 0: continue
        entity=_text(row.get("entity_name",row.get("service_name",row.get("transaction","UNKNOWN")))) or "UNKNOWN"
        service=_text(row.get("service_name")); transaction=_text(row.get("transaction"))
        ranked.append({"entity":entity,"score":round(score,4),"reason":f"Degrading latency {latency:.2f}%, errors {errors:.2f}%, absolute resource pressure {resource:.2f}%","impacted_services":[service] if service else [],"impacted_transactions":[transaction] if transaction else [],"source":row})
    ranked.sort(key=lambda x:x["score"],reverse=True); ranked=ranked[:max(0,limit)]; primary=ranked[0] if ranked else None
    return {"run_id":_text(run_id),"primary":primary,"secondary":ranked[1:],"confidence":round(min(1.0,primary["score"]/100 if primary else 0.0),3),"count":len(ranked),"status":"BOTTLENECK_OBSERVED" if primary else "NO_DEGRADING_BOTTLENECK"}

def identify_bottlenecks(rows, *, run_id=None, limit=10):
    a=analyze_bottlenecks(rows,run_id=_text(run_id),limit=limit); items=([a["primary"]] if a.get("primary") else [])+a.get("secondary",[])
    return [dict(x["source"],bottleneck_score=x["score"],impact="CRITICAL" if x["score"]>=100 else "WARNING") for x in items]

def persist_bottleneck_intelligence(client: Any, analysis: Mapping[str, Any], *, measurement: str="aiperf_bottleneck_intelligence") -> None:
    run_id=_text(analysis["run_id"]); client.query(f'DROP SERIES FROM "{measurement}" WHERE "run_id"=\'{run_id}\'')
    items=[]
    if analysis.get("primary"): items.append(("primary",analysis["primary"]))
    items.extend(("secondary",x) for x in analysis.get("secondary",[]))
    if not items:
        items=[("none",{"entity":"NONE","score":0.0,"reason":"No degrading bottleneck identified.","impacted_services":[],"impacted_transactions":[]})]
    points=[{"measurement":measurement,"tags":{"run_id":run_id,"role":role,"entity":_text(item.get("entity","NONE"))},"fields":{"confidence":float(analysis.get("confidence",0)),"reason":_text(item.get("reason","No bottleneck identified")),"impacted_services":",".join(item.get("impacted_services",[])),"impacted_transactions":",".join(item.get("impacted_transactions",[])),"score":float(item.get("score",0)),"status":_text(analysis.get("status","UNKNOWN"))}} for role,item in items]
    if client.write_points(points) is False: raise RuntimeError("InfluxDB rejected bottleneck intelligence")

def summarize_bottlenecks(analysis: Mapping[str, Any]) -> dict[str, Any]:
    p=analysis.get("primary")
    if not p: return {"primary_bottleneck":"","secondary_bottleneck":"","confidence":0.0,"reason":"No degrading bottleneck identified from available evidence.","impacted_services":[],"impacted_transactions":[],"status":"NO_DEGRADING_BOTTLENECK"}
    sec=analysis.get("secondary") or []
    return {"primary_bottleneck":p["entity"],"secondary_bottleneck":sec[0]["entity"] if sec else "","confidence":analysis.get("confidence",0.0),"reason":p["reason"],"impacted_services":p.get("impacted_services",[]),"impacted_transactions":p.get("impacted_transactions",[]),"status":"BOTTLENECK_OBSERVED"}

def query_bottlenecks(client: Any, run_id: str, measurements=("aiperf_transaction_comparison","aiperf_service_comparison")):
    rows=[]
    for measurement in measurements: rows.extend(client.query(f'SELECT * FROM "{measurement}" WHERE "current_run_id"=\'{run_id}\' OR "run_id"=\'{run_id}\'').get_points())
    return rows

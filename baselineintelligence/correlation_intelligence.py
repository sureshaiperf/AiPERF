"""Correlations, root cause and business impact for one RUN_ID."""
from __future__ import annotations
import math
from typing import Any

def calculate_correlations(rows, metrics=None, *, minimum_samples=3):
    rows = [dict(r) for r in rows]; names = list(metrics or (rows[0].keys() if rows else ())); out=[]
    for i,a in enumerate(names):
        for b in names[i+1:]:
            pairs=[]
            for r in rows:
                try:
                    x,y=float(r[a]),float(r[b])
                    if math.isfinite(x) and math.isfinite(y): pairs.append((x,y))
                except (KeyError,TypeError,ValueError): pass
            if len(pairs)<minimum_samples: continue
            ax=sum(x for x,_ in pairs)/len(pairs); by=sum(y for _,y in pairs)/len(pairs)
            den=math.sqrt(sum((x-ax)**2 for x,_ in pairs)*sum((y-by)**2 for _,y in pairs))
            if den: out.append({"metric_a":a,"metric_b":b,"correlation":round(sum((x-ax)*(y-by) for x,y in pairs)/den,6),"sample_count":len(pairs)})
    return sorted(out,key=lambda x:abs(x["correlation"]),reverse=True)

def analyze_correlation(run_id: str, *, variance=None, anomaly=None, bottleneck=None, release=None, service=None, rows=()):
    correlations=calculate_correlations(rows)
    root = (bottleneck or {}).get("primary") or (correlations[0] if correlations else None)
    root_text = f"{root.get('entity', root.get('metric_a', 'unknown'))} is the leading correlated signal." if root else "Insufficient correlated signals."
    return {"run_id":str(run_id),"correlations":correlations,"root_cause":root_text,"business_impact": (release or {}).get("rationale","Assess release risk using observed performance signals."),"inputs":{"variance":variance or [],"anomaly":anomaly or {},"bottleneck":bottleneck or {},"release":release or {},"service":service or {}}}

def persist_correlation_intelligence(client: Any, result, *, measurement="aiperf_correlation_intelligence"):
    client.write_points([{"measurement":measurement,"tags":{"run_id":str(result["run_id"])},"fields":{"correlations":str(result["correlations"]),"root_cause":result["root_cause"],"business_impact":result["business_impact"]}}])

def summarize_correlations(result):
    correlations = result.get("correlations", [])
    confidence = abs(correlations[0]["correlation"]) if correlations else 0.0
    return {
        "summary": (
            f"Correlated {len(correlations)} signal pair(s). "
            f"{result['root_cause']}"
        ),
        "root_cause_hypothesis": result["root_cause"],
        "confidence": round(confidence, 3),
        "business_impact": result["business_impact"],
    }

def query_correlations(client, run_id, measurement="aiperf_execution_history"):
    return calculate_correlations(client.query(f'SELECT * FROM "{measurement}" WHERE run_id=\'{run_id}\'').get_points())

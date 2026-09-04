"""Search persisted findings by RUN_ID, text, or optional embeddings."""
from __future__ import annotations

from typing import Any
import json
try:
    from .embeddings_knowledge_layer import cosine_similarity, load_findings
except ImportError:
    from embeddings_knowledge_layer import cosine_similarity, load_findings


def search_findings(client: Any, query: str, *, run_id: str | None = None,
                    limit: int = 10, query_vector: list[float] | None = None) -> list[dict[str, Any]]:
    try:
        rows = load_findings(client)
    except Exception:
        rows = []
    # Findings packages remain searchable even when embedding generation was
    # disabled (for example in an air-gapped CI run).
    if not rows:
        try:
            package_rows = client.query('SELECT * FROM "aiperf_findings_package"').get_points()
        except Exception:
            package_rows = []
        for row in package_rows:
            row["text"] = row.get("summary", row.get("findings_json", ""))
            row["embedding"] = []
    terms = query.lower().split()
    matches = []
    for row in rows:
        if run_id is not None and str(row.get("run_id")) != str(run_id):
            continue
        text = str(row.get("text", "")).lower()
        lexical = sum(term in text for term in terms)
        semantic = cosine_similarity(query_vector, row["embedding"]) if query_vector and row.get("embedding") else 0.0
        if lexical or semantic:
            item = dict(row)
            item["relevance"] = round(semantic + lexical / max(len(terms), 1), 6)
            matches.append(item)
    return sorted(matches, key=lambda item: item["relevance"], reverse=True)[:max(0, limit)]

def find_similar_findings(client: Any, run_id: str, *, limit: int = 5,
                          query_vector: list[float] | None = None) -> list[dict[str, Any]]:
    """Return the top five historical findings and persist similarity results."""
    rows = load_findings(client)
    current = next((r for r in rows if str(r.get("run_id")) == str(run_id)), None)
    vector = query_vector or (current or {}).get("embedding", [])
    results = []
    for row in rows:
        if str(row.get("run_id")) == str(run_id): continue
        item = dict(row)
        item["similarity_score"] = round(cosine_similarity(vector, row.get("embedding", [])), 6)
        item["risk"] = row.get("risk_level", row.get("risk", "UNKNOWN"))
        recommendations = row.get("recommendations", [])
        try:
            recommendations = json.loads(recommendations)
        except (TypeError, json.JSONDecodeError):
            pass
        item["recommendations"] = recommendations
        item["outcomes"] = row.get("outcomes", row.get("outcome", "UNKNOWN"))
        results.append(item)
    results = sorted(results, key=lambda x: x["similarity_score"], reverse=True)[:limit]
    if results:
        client.write_points([{"measurement":"aiperf_historical_similarity",
            "tags":{"run_id":str(run_id), "similar_run_id":str(r.get("run_id","UNKNOWN"))},
            "fields":{"similarity_score":float(r["similarity_score"]), "risk":str(r["risk"]),
                      "recommendations":str(r["recommendations"]), "outcomes":str(r["outcomes"])}} for r in results])
    return results

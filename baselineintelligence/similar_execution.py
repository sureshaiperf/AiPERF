"""AiPERF Similar Execution Intelligence with throughput semantic version 2."""

from __future__ import annotations

import math
import os
import re
import sys
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from influxdb import InfluxDBClient
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import StandardScaler


INFLUX_HOST = os.getenv("INFLUX_HOST", "localhost")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DATABASE", os.getenv("INFLUX_DB", "jmeter"))
MINIMUM_EXECUTIONS = int(os.getenv("AIPERF_MIN_SIMILAR_EXECUTIONS", "5"))

FEATURE_COLUMNS = [
    "avg_rt", "p95", "p99", "throughput_rps", "error_rate",
    "gateway_heap_pct", "gateway_active_requests", "gateway_executor_active",
    "gateway_avg_response_time_ms", "gateway_request_count",
    "user_heap_pct", "user_active_requests", "user_executor_active",
    "user_avg_response_time_ms", "user_request_count",
    "product_heap_pct", "product_active_requests", "product_executor_active",
    "product_avg_response_time_ms", "product_request_count",
    "order_heap_pct", "order_active_requests", "order_executor_active",
    "order_avg_response_time_ms", "order_request_count",
]


def finite_number(value: Any) -> Optional[float]:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def safe_run_id(value: Any) -> str:
    run_id = str(value or "").strip()
    if not run_id:
        raise ValueError(
            "RUN_ID must be provided as the first CLI argument or environment variable"
        )
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", run_id):
        raise ValueError("RUN_ID contains unsupported characters")
    return run_id


def create_client() -> InfluxDBClient:
    return InfluxDBClient(
        host=INFLUX_HOST,
        port=INFLUX_PORT,
        database=INFLUX_DB,
        timeout=int(os.getenv("INFLUX_TIMEOUT_SECONDS", "30")),
    )


def load_execution_history(client: InfluxDBClient) -> Dict[str, Dict[str, Any]]:
    rows = list(
        client.query('SELECT * FROM "aiperf_execution_history"').get_points()
    )
    latest_by_run: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        run_id = str(row.get("run_id") or "").strip()
        if run_id:
            latest_by_run[run_id] = dict(row)
    return latest_by_run


def resolve_semantics(
    fingerprint: Dict[str, Any],
    history: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    sample_count = finite_number(fingerprint.get("sample_count"))
    throughput_rps = finite_number(fingerprint.get("throughput_rps"))
    semantic_version = int(
        finite_number(fingerprint.get("throughput_semantic_version")) or 1
    )

    if history:
        if sample_count is None:
            sample_count = finite_number(history.get("sample_count"))
        if throughput_rps is None:
            throughput_rps = finite_number(history.get("throughput_rps"))

        history_version = int(
            finite_number(history.get("throughput_semantic_version")) or 1
        )
        history_throughput = finite_number(history.get("throughput"))
        duration = finite_number(
            history.get("test_duration_seconds")
            or history.get("duration_seconds")
        )

        if sample_count is None and history_version < 2:
            sample_count = history_throughput
        if throughput_rps is None and history_version >= 2:
            throughput_rps = history_throughput
        if throughput_rps is None and sample_count is not None and duration and duration > 0:
            throughput_rps = sample_count / duration

    legacy_throughput = finite_number(fingerprint.get("throughput"))
    if semantic_version >= 2 and throughput_rps is None:
        throughput_rps = legacy_throughput
    elif semantic_version < 2 and sample_count is None:
        sample_count = legacy_throughput

    return {
        "sample_count": sample_count or 0.0,
        "throughput_rps": throughput_rps or 0.0,
        "throughput_semantic_version": 2 if throughput_rps is not None else 1,
    }


def percent_change(current: float, historical: float) -> float:
    if historical == 0:
        return 0.0
    return round(((current - historical) / abs(historical)) * 100.0, 2)


def main() -> int:
    current_run_id = safe_run_id(
        sys.argv[1] if len(sys.argv) > 1 else os.getenv("RUN_ID")
    )
    client = create_client()

    try:
        client.ping()
        points = list(
            client.query('SELECT * FROM "aiperf_execution_fingerprint"').get_points()
        )
        if len(points) < MINIMUM_EXECUTIONS:
            print("\n===================================")
            print("SIMILAR EXECUTION INTELLIGENCE")
            print("===================================")
            print(
                f"WARNING: Only {len(points)} execution(s) available. Minimum "
                f"{MINIMUM_EXECUTIONS} executions required for similarity analysis."
            )
            print("Skipping Similar Execution Intelligence.")
            return 0

        history_by_run = load_execution_history(client)
        enriched: List[Dict[str, Any]] = []
        for point in points:
            row = dict(point)
            run_id = str(row.get("run_id") or "").strip()
            semantics = resolve_semantics(row, history_by_run.get(run_id))
            row.update(semantics)
            enriched.append(row)

        df = pd.DataFrame(enriched).sort_values("time").reset_index(drop=True)
        original_df = df.copy()

        for column in FEATURE_COLUMNS:
            if column not in df.columns:
                df[column] = 0.0
                original_df[column] = 0.0

        numeric = df[FEATURE_COLUMNS].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        scaled = StandardScaler().fit_transform(numeric)
        scaled_df = pd.DataFrame(scaled, columns=FEATURE_COLUMNS, index=df.index)

        current_indices = df.index[df["run_id"].astype(str) == current_run_id]
        if len(current_indices) == 0:
            raise ValueError(f"No fingerprint found for RUN_ID={current_run_id}")
        current_index = int(current_indices[-1])
        current_vector = scaled_df.loc[current_index, FEATURE_COLUMNS].to_numpy().reshape(1, -1)
        current_original = original_df.loc[current_index]

        similarity_results: List[Dict[str, Any]] = []
        for index in df.index:
            if index == current_index:
                continue
            historical_vector = scaled_df.loc[index, FEATURE_COLUMNS].to_numpy().reshape(1, -1)
            historical_original = original_df.loc[index]
            raw_score = float(cosine_similarity(current_vector, historical_vector)[0][0])
            similarity_score = round(((raw_score + 1.0) / 2.0) * 100.0, 2)
            similarity_results.append(
                {
                    "current_run_id": current_run_id,
                    "similar_run_id": str(historical_original["run_id"]),
                    "similarity_score": similarity_score,
                    "current_avg_rt": float(current_original["avg_rt"]),
                    "historical_avg_rt": float(historical_original["avg_rt"]),
                    "current_p95": float(current_original["p95"]),
                    "historical_p95": float(historical_original["p95"]),
                    "current_throughput_rps": float(current_original["throughput_rps"]),
                    "historical_throughput_rps": float(historical_original["throughput_rps"]),
                    "current_sample_count": int(float(current_original["sample_count"])),
                    "historical_sample_count": int(float(historical_original["sample_count"])),
                }
            )

        top3 = (
            pd.DataFrame(similarity_results)
            .sort_values("similarity_score", ascending=False)
            .head(3)
        )
        if top3.empty:
            raise RuntimeError("No historical execution is available for comparison")

        print(f"Total Fingerprints Found : {len(df)}")
        print(f"Current Run : {current_run_id}")
        print("\n===================================")
        print("TOP 3 SIMILAR EXECUTIONS")
        print("===================================")
        print(top3[["similar_run_id", "similarity_score"]].to_string(index=False))

        client.query(
            'DROP SERIES FROM "aiperf_similar_execution" '
            f"WHERE \"current_run_id\"='{current_run_id}'"
        )
        points_to_write = []
        for _, row in top3.iterrows():
            points_to_write.append(
                {
                    "measurement": "aiperf_similar_execution",
                    "tags": {
                        "current_run_id": str(row["current_run_id"]),
                        "similar_run_id": str(row["similar_run_id"]),
                        "throughput_semantic_version": "2",
                    },
                    "fields": {
                        "similarity_score": float(row["similarity_score"]),
                        "current_throughput_rps": float(row["current_throughput_rps"]),
                        "historical_throughput_rps": float(row["historical_throughput_rps"]),
                        "current_sample_count": int(row["current_sample_count"]),
                        "historical_sample_count": int(row["historical_sample_count"]),
                    },
                }
            )
        if client.write_points(points_to_write) is False:
            raise RuntimeError("InfluxDB rejected similar-execution records")

        best = top3.iloc[0]
        avg_rt_change = percent_change(best["current_avg_rt"], best["historical_avg_rt"])
        p95_change = percent_change(best["current_p95"], best["historical_p95"])
        throughput_change = percent_change(
            best["current_throughput_rps"],
            best["historical_throughput_rps"],
        )

        if p95_change > 20:
            insight = (
                "Potential performance regression detected. "
                f"P95 increased by {p95_change}% compared with the closest "
                "historical execution."
            )
        elif p95_change < -20:
            insight = (
                "Performance improvement detected. "
                f"P95 improved by {abs(p95_change)}% compared with the closest "
                "historical execution."
            )
        else:
            insight = "Performance behaviour is consistent with the closest historical execution."

        print("\n===================================")
        print("AI INSIGHT")
        print("===================================")
        print(f"Current Run                 : {current_run_id}")
        print(f"Closest Match               : {best['similar_run_id']}")
        print(f"Similarity Score            : {best['similarity_score']}%")
        print(f"Current Avg RT              : {best['current_avg_rt']:.3f} ms")
        print(f"Historical Avg RT           : {best['historical_avg_rt']:.3f} ms")
        print(f"Avg RT Change               : {avg_rt_change:.2f}%")
        print(f"Current P95                 : {best['current_p95']:.3f} ms")
        print(f"Historical P95              : {best['historical_p95']:.3f} ms")
        print(f"P95 Change                  : {p95_change:.2f}%")
        print(f"Current Throughput          : {best['current_throughput_rps']:.2f} requests/second")
        print(f"Historical Throughput       : {best['historical_throughput_rps']:.2f} requests/second")
        print(f"Throughput Change           : {throughput_change:.2f}%")
        print(f"Current Sample Count        : {int(best['current_sample_count']):,}")
        print(f"Historical Sample Count     : {int(best['historical_sample_count']):,}")
        print(f"AI Insight                  : {insight}")
        print("\nSimilarity Analysis Completed Successfully.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Similar Execution Intelligence Failed")
        print(f"Error Type: {type(exc).__name__}")
        print(f"Error: {exc}")
        raise SystemExit(1)

import json
from datetime import datetime, UTC

try:
    from influxdb import InfluxDBClient
except ImportError:  # pragma: no cover - allows analysis helpers without the client
    InfluxDBClient = None



try:
    from .anomaly_detection import OUTPUT_MEASUREMENT
except ImportError:
    from anomaly_detection import OUTPUT_MEASUREMENT

try:
    from .bottleneck_intelligence import query_bottlenecks, analyze_bottlenecks, persist_bottleneck_intelligence, summarize_bottlenecks
    from .correlation_intelligence import query_correlations, analyze_correlation, persist_correlation_intelligence, summarize_correlations
    from .embeddings_knowledge_layer import create_embedding, package_to_text, store_finding
    from .historical_findings_search import find_similar_findings
except ImportError:
    from bottleneck_intelligence import query_bottlenecks, analyze_bottlenecks, persist_bottleneck_intelligence, summarize_bottlenecks
    from correlation_intelligence import query_correlations, analyze_correlation, persist_correlation_intelligence, summarize_correlations
    from embeddings_knowledge_layer import create_embedding, package_to_text, store_finding
    from historical_findings_search import find_similar_findings

client = None
DB_HOST = "localhost"
DB_PORT = 8086
DB_NAME = "jmeter"

MEASUREMENT_NAME = "aiperf_findings_package"
FINDINGS_PACKAGE_VERSION = "1.1"


# ==========================================================

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def get_latest_run_id():

    query = """
    SELECT *
    FROM aiperf_variance_ranking
    """

    result = client.query(query)

    rows = list(result.get_points())

    if not rows:
        raise Exception(
            "No records found in aiperf_variance_ranking"
        )

    rows.sort(
        key=lambda x: x.get("time", ""),
        reverse=True
    )

    return rows[0]["run_id"]


def get_top_variances(run_id):

    query = f"""
    SELECT *
    FROM aiperf_variance_ranking
    WHERE run_id='{run_id}'
    """

    result = client.query(query)

    rows = list(result.get_points())

    rows.sort(
        key=lambda x: abs(float(x.get("variance_pct", 0))),
        reverse=True
    )

    return rows[:10]


def get_transaction_impacts(run_id):

    query = f"""
    SELECT *
    FROM aiperf_transaction_comparison
    WHERE current_run_id='{run_id}'
    """

    result = client.query(query)

    return list(result.get_points())


def get_service_impacts(run_id):

    query = f"""
    SELECT *
    FROM aiperf_service_comparison
    WHERE current_run_id='{run_id}'
    """

    result = client.query(query)

    return list(result.get_points())


def get_ai_insights(run_id):

    query = f"""
    SELECT *
    FROM aiperf_ai_insights
    WHERE run_id='{run_id}'
    """

    result = client.query(query)

    return list(result.get_points())


def get_anomalies(run_id):

    query = f"""
    SELECT *
    FROM {OUTPUT_MEASUREMENT}
    WHERE run_id='{run_id}'
    """

    result = client.query(query)

    return list(result.get_points())


def build_anomaly_summary(anomalies):

    if not anomalies:

        return {
            "status": "NO_DATA",
            "total_metrics": 0,
            "anomaly_count": 0,
            "critical_count": 0,
            "warning_count": 0,
            "message": "No anomaly detection results were found for this run."
        }

    detected = [
        item for item in anomalies
        if item.get("is_anomaly", 0) in (1, True, "1", "true", "True")
    ]
    critical_count = sum(
        item.get("severity") == "CRITICAL"
        for item in detected
    )
    warning_count = sum(
        item.get("severity") == "WARNING"
        for item in detected
    )

    if critical_count:
        status = "CRITICAL"
    elif warning_count:
        status = "WARNING"
    else:
        status = "NORMAL"

    if detected:
        metrics = ", ".join(
            f"{item.get('metric', 'UNKNOWN')} "
            f"({float(item.get('deviation_pct', 0) or 0):.2f}%)"
            for item in detected
        )
        message = (
            f"{len(detected)} anomalous metric(s) detected: {metrics}."
        )
    else:
        message = "No anomalous metrics detected."

    return {
        "status": status,
        "total_metrics": len(anomalies),
        "anomaly_count": len(detected),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "message": message
    }


def build_summary(top_variances, anomaly_summary=None):

    """Create a concise executive summary from variance records."""

    anomaly_summary = anomaly_summary or build_anomaly_summary([])

    if not top_variances:
        summary = "No performance variance records were found for the latest run."
    else:
        regressions = [
            item for item in top_variances
            if float(item.get("variance_pct", 0)) > 0
        ]
        improvements = [
            item for item in top_variances
            if float(item.get("variance_pct", 0)) < 0
        ]

        largest = max(
            top_variances,
            key=lambda item: abs(float(item.get("variance_pct", 0)))
        )
        variance = float(largest.get("variance_pct", 0))
        direction = "regression" if variance > 0 else "improvement"

        summary = (
            f"Analyzed {len(top_variances)} top variance(s): "
            f"{len(regressions)} regression(s) and {len(improvements)} improvement(s). "
            f"The largest {direction} was "
            f"{largest.get('entity_name', 'UNKNOWN')} / "
            f"{largest.get('metric', 'UNKNOWN')} at {variance:.2f}%."
        )

    if anomaly_summary["anomaly_count"]:
        summary += f" {anomaly_summary['message']}"
    elif anomaly_summary["status"] == "NORMAL":
        summary += " No anomalous metrics were detected."

    return summary


# ==========================================================
# TOP REGRESSIONS
# ==========================================================

def get_top_regressions(top_variances):

    regressions = [
        x for x in top_variances
        if float(x.get("variance_pct", 0)) > 0
    ]

    regressions.sort(
        key=lambda x: float(x.get("variance_pct", 0)),
        reverse=True
    )

    return regressions[:5]


# ==========================================================
# TOP IMPROVEMENTS
# ==========================================================

def get_top_improvements(top_variances):

    improvements = [
        x for x in top_variances
        if float(x.get("variance_pct", 0)) < 0
    ]

    improvements.sort(
        key=lambda x: float(x.get("variance_pct", 0))
    )

    return improvements[:5]


# ==========================================================
# SERVICE HEALTH
# ==========================================================

def build_service_health(service_impacts):

    health = {}

    for service in service_impacts:

        service_name = service.get(
            "service_name",
            "UNKNOWN"
        )

        rt_variance = abs(
            float(
                service.get(
                    "avg_rt_variance_pct",
                    0
                )
            )
        )

        heap_variance = abs(
            float(
                service.get(
                    "heap_pct_variance_pct",
                    0
                )
            )
        )

        score = max(
            rt_variance,
            heap_variance
        )

        if score >= 50:

            status = "CRITICAL"

        elif score >= 20:

            status = "WARNING"

        else:

            status = "HEALTHY"

        health[service_name] = {
            "status": status,
            "variance_score": round(
                score,
                2
            )
        }

    return health


# ==========================================================
# RISK ASSESSMENT
# ==========================================================

def calculate_risk(top_regressions, anomaly_summary=None):

    anomaly_summary = anomaly_summary or build_anomaly_summary([])

    if not top_regressions:

        anomaly_score = min(
            anomaly_summary["critical_count"] * 30
            + anomaly_summary["warning_count"] * 10,
            40
        )
        level = "HIGH" if anomaly_score >= 30 else (
            "MEDIUM" if anomaly_score else "LOW"
        )
        return {
            "level": level,
            "score": anomaly_score,
            "variance_score": 0,
            "anomaly_contribution": anomaly_score
        }

    highest_variance = max(
        [
            abs(float(x.get("variance_pct", 0)))
            for x in top_regressions
        ]
    )

    variance_score = int(highest_variance / 5)
    anomaly_score = min(
        anomaly_summary["critical_count"] * 30
        + anomaly_summary["warning_count"] * 10,
        40
    )
    risk_score = min(variance_score + anomaly_score, 100)

    if risk_score >= 80:

        level = "CRITICAL"

    elif risk_score >= 60:

        level = "HIGH"

    elif risk_score >= 30:

        level = "MEDIUM"

    else:

        level = "LOW"

    return {
        "level": level,
        "score": risk_score,
        "variance_score": variance_score,
        "anomaly_contribution": anomaly_score
    }


def calculate_release_impact(risk, anomaly_summary, service_health):

    """Translate technical findings into a release decision."""
    critical_services = sum(
        details["status"] == "CRITICAL"
        for details in service_health.values()
    )
    release_blocked = (
        risk["level"] == "CRITICAL"
        or anomaly_summary["critical_count"] > 0
        or critical_services > 0
    )

    if release_blocked:
        decision = "BLOCK"
        rationale = "Critical performance risk requires remediation before release."
    elif risk["level"] in ("HIGH", "MEDIUM") or anomaly_summary["warning_count"]:
        decision = "CONDITIONAL"
        rationale = "Release requires review and documented acceptance of observations."
    else:
        decision = "PROCEED"
        rationale = "No critical release-blocking performance signals were detected."

    return {
        "decision": decision,
        "release_blocked": release_blocked,
        "rationale": rationale,
        "risk_level": risk["level"],
        "anomaly_status": anomaly_summary["status"],
        "critical_service_count": critical_services
    }


# ==========================================================
# RECOMMENDED ACTIONS
# ==========================================================

def generate_recommendations(
    top_regressions,
    service_health,
    anomaly_summary=None
):
    anomaly_summary = anomaly_summary or build_anomaly_summary([])


    recommendations = []

    for regression in top_regressions[:3]:

        recommendations.append(
            f"Investigate "
            f"{regression.get('entity_name')} "
            f"{regression.get('metric')} variance "
            f"({round(float(regression.get('variance_pct', 0)), 2)}%)."
        )

    for service, details in service_health.items():

        if details["status"] != "HEALTHY":

            recommendations.append(
                f"Review {service} service metrics "
                f"due to {details['status']} status."
            )

    if anomaly_summary["anomaly_count"]:

        recommendations.append(
            "Investigate anomalous metrics: "
            f"{anomaly_summary['message']}"
        )

    if not recommendations:

        recommendations.append(
            "No major performance concerns identified."
        )

    return recommendations


# ==========================================================
# MAIN
# ==========================================================


def main(influx_client=None):
    global client
    if influx_client is None and InfluxDBClient is None:
        raise RuntimeError("influxdb package is required to run the findings package")
    client = influx_client or InfluxDBClient(host=DB_HOST, port=DB_PORT, database=DB_NAME)
    try:

        print("\n===================================")
        print("AiPERF FINDINGS PACKAGE")
        print("===================================\n")

        latest_run_id = get_latest_run_id()

        print(f"Latest Run ID : {latest_run_id}")

        top_variances = get_top_variances(
            latest_run_id
        )

        transaction_impacts = get_transaction_impacts(
            latest_run_id
        )

        service_impacts = get_service_impacts(
            latest_run_id
        )

        anomalies = get_anomalies(
            latest_run_id
        )

        anomaly_summary = build_anomaly_summary(
            anomalies
        )

        service_health = build_service_health(
            service_impacts
        )

        try:
            bottleneck_rows = query_bottlenecks(client, latest_run_id)
            bottlenecks = analyze_bottlenecks(bottleneck_rows, run_id=latest_run_id)
            persist_bottleneck_intelligence(client, bottlenecks)
        except Exception:
            bottlenecks = {"run_id": latest_run_id, "primary": None, "secondary": [], "confidence": 0.0}

        ai_insights = get_ai_insights(
            latest_run_id
        )

        summary_text = build_summary(
            top_variances,
            anomaly_summary
        )

        top_regressions = get_top_regressions(
        top_variances
        )

        top_improvements = get_top_improvements(
            top_variances
        )

        risk = calculate_risk(
            top_regressions,
            anomaly_summary
        )

        release_impact = calculate_release_impact(
            risk,
            anomaly_summary,
            service_health
        )

        try:
            correlations = analyze_correlation(
                latest_run_id,
                variance=top_variances,
                anomaly=anomaly_summary,
                bottleneck=bottlenecks,
                release=release_impact,
                service=service_health
            )
            persist_correlation_intelligence(client, correlations)
        except Exception:
            correlations = {
                "run_id": latest_run_id,
                "correlations": [],
                "root_cause": "Insufficient correlated signals.",
                "business_impact": "Unable to assess correlated release impact."
            }

        recommended_actions = generate_recommendations(
            top_regressions,
            service_health,
            anomaly_summary
        )


        findings_package = {

        "run_id": latest_run_id,

        "package_version":
            FINDINGS_PACKAGE_VERSION,

        "generated_time":
            datetime.now(UTC).isoformat(),

        "executive_summary":
            summary_text,

        "risk":
            risk,

        "release_impact":
            release_impact,

        "top_regressions":
            top_regressions,

        "top_improvements":
            top_improvements,

        "service_health":
            service_health,

        "anomaly_summary":
            anomaly_summary,

        "bottleneck_intelligence":
            bottlenecks,

        "bottleneck_summary":
            summarize_bottlenecks(bottlenecks),

        "correlation_intelligence":
            correlations,

        "correlation_summary":
            summarize_correlations(correlations),

        "anomalies":
            anomalies,

        "recommended_actions":
            recommended_actions,

        "top_variances":
            top_variances,

        "transaction_impacts":
            transaction_impacts,

        "service_impacts":
            service_impacts,

        "ai_insights": [

            x.get("insight_text")

            for x in ai_insights

            if x.get("insight_text")

        ]
    }

        print("\n===================================")
        print("EXECUTIVE SUMMARY")
        print("===================================\n")

        print(summary_text)

        print("\nANOMALY SUMMARY")
        print("-----------------------------")
        print(anomaly_summary["message"])

        print("\nRISK ASSESSMENT")
        print("-----------------------------")
        print(
            f"{risk['level']} "
            f"(Score={risk['score']})"
        )

        print("\nRELEASE IMPACT")
        print("-----------------------------")
        print(
            f"{release_impact['decision']} - "
            f"{release_impact['rationale']}"
        )

        print("\nTOP REGRESSIONS")
        print("-----------------------------")

        for item in top_regressions:

            print(
                f"{item.get('entity_name')} | "
                f"{item.get('metric')} | "
                f"{round(float(item.get('variance_pct', 0)),2)}%"
            )

        print("\nTOP IMPROVEMENTS")
        print("-----------------------------")

        for item in top_improvements:

            print(
                f"{item.get('entity_name')} | "
                f"{item.get('metric')} | "
                f"{round(float(item.get('variance_pct', 0)),2)}%"
            )

        print("\nRECOMMENDED ACTIONS")
        print("-----------------------------")

        for action in recommended_actions:

            print(f"- {action}")


        findings_json = json.dumps(
            findings_package,
            indent=2,
            default=str
        )

        embedding_vector = create_embedding(
            package_to_text(findings_package)
        )
        if embedding_vector:
            store_finding(
                client,
                latest_run_id,
                package_to_text(findings_package),
                embedding_vector,
                risk_level=risk["level"],
                recommendations=recommended_actions,
                outcome=release_impact["decision"]
            )
            find_similar_findings(client, latest_run_id, query_vector=embedding_vector)

        json_body = [
            {
                "measurement": MEASUREMENT_NAME,

                "tags": {
                    "run_id": latest_run_id
                },

                "fields": {
                    "summary": summary_text,
                    "findings_json": findings_json
                }
            }
        ]

        client.write_points(json_body)

        print("\n===================================")
        print("FINDINGS PACKAGE GENERATED")
        print("===================================\n")

        print(f"RUN_ID              : {latest_run_id}")
        print(f"Top Variances       : {len(top_variances)}")
        print(f"Transactions        : {len(transaction_impacts)}")
        print(f"Services            : {len(service_impacts)}")
        print(f"AI Insights         : {len(ai_insights)}")

        print("\nWritten To:")
        print("aiperf_findings_package")

    except Exception as e:

        print("\n===================================")
        print("FINDINGS PACKAGE FAILED")
        print("===================================\n")

        print(str(e))

        raise

    finally:

        client.close()



if __name__ == "__main__":
    main()

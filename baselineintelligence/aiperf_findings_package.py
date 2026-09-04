import json
from datetime import datetime, UTC

from influxdb import InfluxDBClient


# ==========================================================
# CONFIGURATION
# ==========================================================

DB_HOST = "localhost"
DB_PORT = 8086
DB_NAME = "jmeter"

MEASUREMENT_NAME = "aiperf_findings_package"


# ==========================================================
# INFLUXDB CONNECTION
# ==========================================================

client = InfluxDBClient(
    host=DB_HOST,
    port=DB_PORT,
    database=DB_NAME
)


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


def build_summary(top_variances):

    """Create a concise executive summary from variance records."""

    if not top_variances:
        return "No performance variance records were found for the latest run."

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

    return (
        f"Analyzed {len(top_variances)} top variance(s): "
        f"{len(regressions)} regression(s) and {len(improvements)} improvement(s). "
        f"The largest {direction} was "
        f"{largest.get('entity_name', 'UNKNOWN')} / "
        f"{largest.get('metric', 'UNKNOWN')} at {variance:.2f}%."
    )


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

def calculate_risk(top_regressions):

    if not top_regressions:

        return {
            "level": "LOW",
            "score": 0
        }

    highest_variance = max(
        [
            abs(float(x.get("variance_pct", 0)))
            for x in top_regressions
        ]
    )

    risk_score = min(
        int(highest_variance / 5),
        100
    )

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
        "score": risk_score
    }


# ==========================================================
# RECOMMENDED ACTIONS
# ==========================================================

def generate_recommendations(
    top_regressions,
    service_health
):

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

    if not recommendations:

        recommendations.append(
            "No major performance concerns identified."
        )

    return recommendations


# ==========================================================
# MAIN
# ==========================================================

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

    ai_insights = get_ai_insights(
        latest_run_id
    )

    summary_text = build_summary(
        top_variances
    )

    top_regressions = get_top_regressions(
    top_variances
    )

    top_improvements = get_top_improvements(
        top_variances
    )

    service_health = build_service_health(
        service_impacts
    )

    risk = calculate_risk(
        top_regressions
    )

    recommended_actions = generate_recommendations(
        top_regressions,
        service_health
    )


    findings_package = {

    "run_id": latest_run_id,

    "generated_time":
        datetime.now(UTC).isoformat(),

    "executive_summary":
        summary_text,

    "risk":
        risk,

    "top_regressions":
        top_regressions,

    "top_improvements":
        top_improvements,

    "service_health":
        service_health,

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

    print("\nRISK ASSESSMENT")
    print("-----------------------------")
    print(
        f"{risk['level']} "
        f"(Score={risk['score']})"
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
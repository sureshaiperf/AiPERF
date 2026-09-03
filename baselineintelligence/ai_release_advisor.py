#ai_release_advisor.py

# =====================================================
# AiPERF GPT Release Advisor
# =====================================================

from dotenv import load_dotenv
from influxdb import InfluxDBClient

import requests
import os

# =====================================================
# LOAD ENVIRONMENT VARIABLES
# =====================================================

load_dotenv(override=True)

API_URL = os.getenv("API_URL")
API_KEY = os.getenv("API_KEY")

if not API_URL:
    raise Exception(
        "API_URL environment variable not found"
    )

if not API_KEY:
    raise Exception(
        "API_KEY environment variable not found"
    )

# =====================================================
# GPT CALL
# =====================================================

def call_gpt(prompt):

    payload = {
        "model": "gpt-5-chat",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.3,
        "top_p": 0.9,
        "max_tokens": 500
    }

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": API_KEY
    }

    response = requests.post(
        API_URL,
        headers=headers,
        json=payload,
        timeout=120
    )

    response.raise_for_status()

    data = response.json()

    return data["choices"][0]["message"]["content"]

# =====================================================
# RELEASE ADVISOR
# =====================================================

def generate_ai_advice():

    print("\n===================================")
    print("AIPERF GPT RELEASE ADVISOR")
    print("===================================\n")

    # =================================================
    # INFLUXDB
    # =================================================

    client = InfluxDBClient(
        host="localhost",
        port=8086,
        database="jmeter"
    )

    # =================================================
    # GET LATEST RCA
    # =================================================

    query = """
    SELECT *
    FROM aiperf_ai_insights
    WHERE insight_type='rca'
    ORDER BY time DESC
    LIMIT 1
    """

    rows = list(
        client.query(query).get_points()
    )

    if not rows:
        raise Exception(
            "No RCA data found"
        )

    rca = rows[0]

    run_id = rca.get(
        "run_id",
        "UNKNOWN"
    )

    transaction_name = rca.get(
        "transaction_name",
        "UNKNOWN"
    )

    transaction_metric = rca.get(
        "transaction_metric",
        "UNKNOWN"
    )

    transaction_variance_pct = rca.get(
        "transaction_variance_pct",
        0
    )

    service_name = rca.get(
        "service_name",
        "UNKNOWN"
    )

    service_metric = rca.get(
        "service_metric",
        "UNKNOWN"
    )

    service_variance_pct = rca.get(
        "service_variance_pct",
        0
    )

    rca_text = rca.get(
        "rca_text",
        "No RCA available"
    )

    print(f"Run ID : {run_id}")

    # =================================================
    # GPT PROMPT
    # =================================================

    prompt = f"""
You are an expert Performance Engineering Architect and AI Release Advisor.

Analyze the findings below.

Run ID:
{run_id}

Top Transaction Regression:
Transaction: {transaction_name}
Metric: {transaction_metric}
Variance: {transaction_variance_pct}%

Top Service Regression:
Service: {service_name}
Metric: {service_metric}
Variance: {service_variance_pct}%

Root Cause Analysis:
{rca_text}

Provide your answer in the following format:

Executive Summary:
<summary>

Risk Level:
LOW / MEDIUM / HIGH / CRITICAL

Release Recommendation:
GO / GO WITH CAUTION / NO GO

Root Cause Hypothesis:
<hypothesis>

Recommended Actions:
<actions>

Keep the response concise and executive friendly.
"""

    # =================================================
    # CALL GPT
    # =================================================

    print("\nCalling GPT...\n")

    response_text = call_gpt(prompt)

    print("\n===================================")
    print("GPT RESPONSE")
    print("===================================\n")

    print(response_text)

    # =================================================
    # SAVE TO INFLUXDB
    # =================================================

    json_body = [

        {
            "measurement": "aiperf_ai_insights",

            "tags": {
                "run_id": run_id,
                "insight_type": "gpt_release_advisor"
            },

            "fields": {

                "insight_text":
                    str(response_text)
            }
        }

    ]

    client.write_points(json_body)

    print("\n===================================")
    print("GPT RELEASE ADVISOR WRITTEN")
    print("===================================\n")

# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    generate_ai_advice()
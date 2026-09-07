# =====================================================
# AiPERF GPT Copilot
# =====================================================

from dotenv import load_dotenv
from influxdb import InfluxDBClient

import requests
import os
import sys


def print_console(value=""):
    """Print text without failing on legacy Windows console encodings."""
    text = str(value)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding))

# =====================================================
# LOAD ENVIRONMENT VARIABLES
# =====================================================

# Keep Jenkins-injected credentials authoritative while supporting local .env runs.
load_dotenv(override=False)

# =====================================================
# PRIMARY MODEL
# =====================================================

PRIMARY_API_URL = os.getenv("PRIMARY_API_URL")
PRIMARY_API_KEY = os.getenv("PRIMARY_API_KEY")
PRIMARY_MODEL = os.getenv(
    "PRIMARY_MODEL",
    "gpt-5-2-chat"
)

# =====================================================
# FAILOVER MODEL
# =====================================================

FAILOVER_API_URL = os.getenv("FAILOVER_API_URL")
FAILOVER_API_KEY = os.getenv("FAILOVER_API_KEY")
FAILOVER_MODEL = os.getenv(
    "FAILOVER_MODEL",
    "gpt-5-mini"
)

# =====================================================
# EMBEDDING MODEL
# =====================================================

EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY")
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "text-embedding-3-large"
)

# =====================================================
# VALIDATION
# =====================================================

if not PRIMARY_API_URL:
    raise Exception(
        "PRIMARY_API_URL environment variable not found"
    )

if not PRIMARY_API_KEY:
    raise Exception(
        "PRIMARY_API_KEY environment variable not found"
    )

if not PRIMARY_MODEL:
    raise Exception(
        "PRIMARY_MODEL environment variable not found"
    )

if not FAILOVER_API_URL:
    raise Exception(
        "FAILOVER_API_URL environment variable not found"
    )

if not FAILOVER_API_KEY:
    raise Exception(
        "FAILOVER_API_KEY environment variable not found"
    )

if not FAILOVER_MODEL:
    raise Exception(
        "FAILOVER_MODEL environment variable not found"
    )

print("\n===================================")
print("AiPERF GPT Configuration")
print("===================================")

print(f"PRIMARY_MODEL  : {PRIMARY_MODEL}")
print(f"FAILOVER_MODEL : {FAILOVER_MODEL}")
print(f"EMBEDDING_MODEL: {EMBEDDING_MODEL}")

print("===================================\n")

# =====================================================
# GENERIC MODEL CALL
# =====================================================

def call_model(
    model,
    api_url,
    api_key,
    prompt
):

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content":
                    "You are AiPERF Copilot, an expert "
                    "Performance Engineering Architect."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.3,
        "top_p": 0.9,
        "max_tokens": 1500
    }

    headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
    }

    response = requests.post(
    api_url,
    headers=headers,
    json=payload,
    timeout=120
)

    print(
        f"MODEL={model} | STATUS={response.status_code}"
    )

    if response.status_code != 200:
        print(
            f"MODEL={model} RESPONSE={response.text}"
        )

    response.raise_for_status()

    data = response.json()

    if "choices" not in data:
        raise Exception(
        f"Unexpected response from model: {data}"
    )

    return data["choices"][0]["message"]["content"]

# =====================================================
# GPT FAILOVER LOGIC
# =====================================================

def call_gpt(prompt):

    models = [

        {
            "model": PRIMARY_MODEL,
            "api_url": PRIMARY_API_URL,
            "api_key": PRIMARY_API_KEY
        },

        {
            "model": FAILOVER_MODEL,
            "api_url": FAILOVER_API_URL,
            "api_key": FAILOVER_API_KEY
        }

    ]

    last_error = None

    for config in models:

        try:

            print("\n===================================")
            print(f"TRYING MODEL : {config['model']}")
            print("===================================\n")

            response = call_model(
                model=config["model"],
                api_url=config["api_url"],
                api_key=config["api_key"],
                prompt=prompt
            )

            print("\n===================================")
            print(f"SUCCESS USING : {config['model']}")
            print("===================================\n")

            return response

        except Exception as ex:

            print("\n===================================")
            print(f"FAILED MODEL : {config['model']}")
            print("===================================\n")

            print(str(ex))

            last_error = ex

    raise Exception(
        f"All configured models failed. Last Error: {last_error}"
    )

# =====================================================
# READ FINDINGS PACKAGE
# =====================================================

def get_latest_findings_package(client, requested_run_id=None):
    if requested_run_id:
        exact_query = f"""
        SELECT *
        FROM aiperf_findings_package
        WHERE run_id='{requested_run_id}'
        ORDER BY time DESC
        LIMIT 1
        """
        exact_rows = list(client.query(exact_query).get_points())
        if exact_rows:
            row = exact_rows[0]
            return requested_run_id, row.get(
                "findings_json",
                "No findings available."
            )
        print_console(
            f"No findings package found for RUN_ID={requested_run_id}; "
            "falling back to the latest package."
        )

    query = """
    SELECT *
    FROM aiperf_findings_package
    ORDER BY time DESC
    LIMIT 1
    """

    rows = list(
        client.query(query).get_points()
    )

    if not rows:

        return (
            "UNKNOWN",
            "No findings package available."
        )

    row = rows[0]

    run_id = row.get(
        "run_id",
        "UNKNOWN"
    )

    findings_json = row.get(
        "findings_json",
        "No findings available."
    )

    return run_id, findings_json


# =====================================================
# EMBEDDING PLACEHOLDER
# FUTURE KNOWLEDGE LAYER
# =====================================================

def create_embedding(text):

    if (
        not EMBEDDING_API_URL
        or not EMBEDDING_API_KEY
        or not EMBEDDING_MODEL
    ):

        print(
            "Embedding configuration not found. "
            "Skipping embedding generation."
        )

        return None

    try:

        payload = {
            "model": EMBEDDING_MODEL,
            "input": text
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {EMBEDDING_API_KEY}"
        }

        response = requests.post(
            EMBEDDING_API_URL,
            headers=headers,
            json=payload,
            timeout=120
        )

        response.raise_for_status()

        print(
            "Embedding generated successfully."
        )

        return response.json()

    except Exception as ex:

        print(
            f"Embedding Error: {str(ex)}"
        )

        return None


# =====================================================
# BUILD GPT CONTEXT
# =====================================================


# =====================================================
# GENERATE AI RESPONSE
# =====================================================

def generate_ai_advice(user_question=None):

    print("\n===================================")
    print("AIPERF GPT COPILOT")
    print("===================================\n")

    client = InfluxDBClient(
        host="localhost",
        port=8086,
        database="jmeter"
    )

    import json

    # =====================================================
    # STEP 1
    # Read latest AiPERF Findings Package
    # This is now the PRIMARY context source for GPT.
    # =====================================================

    print("\n===================================")
    print("LOADING FINDINGS PACKAGE")
    print("===================================\n")

    requested_run_id = os.getenv("RUN_ID")
    run_id, findings_context = get_latest_findings_package(
        client,
        requested_run_id=requested_run_id,
    )
    print(f"RUN_ID : {run_id}")

    print(
        f"FINDINGS SIZE : "
        f"{len(str(findings_context))} characters"
    )

    # =====================================================
    # STEP 2
    # Convert Findings Package JSON into readable format
    # Improves GPT reasoning quality
    # =====================================================

    try:

        findings_context = json.dumps(
            json.loads(findings_context),
            indent=2
        )

        print(
            "Findings package successfully formatted."
        )

    except Exception:

        print(
            "Findings package is not valid JSON. "
            "Proceeding with raw text."
        )

    if not user_question:
        user_question = "Can I release this build?"

    try:

        if (
            EMBEDDING_API_URL
            and EMBEDDING_API_KEY
            and EMBEDDING_MODEL
        ):

            print("\n===================================")
            print("GENERATING EMBEDDINGS")
            print("===================================\n")

            create_embedding(findings_context)

        else:

            print(
                "Embedding configuration not found. "
                "Skipping embedding generation."
            )

    except Exception as ex:

        print(
            f"Embedding Error: {str(ex)}"
        )

    performance_keywords = [
        "release",
        "release readiness",
        "performance",
        "api",
        "latency",
        "throughput",
        "response",
        "p95",
        "p99",
        "baseline",
        "risk",
        "service",
        "bottleneck",
        "execution",
        "regression",
        "transaction",
        "memory",
        "cpu",
        "thread",
        "jvm",
        "anomaly",
        "capacity",
        "sla",
        "availability",
        "scalability",
        "reliability",
        "similar execution"
    ]

    is_performance_question = any(
        keyword in user_question.lower()
        for keyword in performance_keywords
    )

    if is_performance_question:

        prompt = f"""
You are AiPERF Copilot.

You are an expert Chief Performance Architect.

Analyze the execution findings package and answer
using evidence from the package.

RUN ID:
{run_id}

AIPERF FINDINGS PACKAGE:
{findings_context}

USER QUESTION:
{user_question}

The comparison reference run is not automatically a statistically stable
baseline. Treat it as a reference execution unless the package explicitly
provides approved baseline evidence.

Provide:

1. Executive Summary
2. Key Findings
3. Risks
4. Root Cause Assessment
5. Recommendations
6. Release Impact
7. Evidence Gaps and Confidence
8. Concrete Next Actions

Use only the evidence available in the findings package.

Do not invent metrics.

For every important conclusion, name the relevant transaction or service and metric.
Distinguish observed evidence from inference. If evidence is missing, say so.
Respond in markdown with concise tables where they improve readability.
"""

    else:

        prompt = f"""
You are AiPERF Copilot.

User Question:
{user_question}

Answer naturally.

Provide only the answer.
"""

    print("\nCalling AI Engine...\n")

    response_text = call_gpt(prompt)

    print("\n===================================")
    print("AI RESPONSE")
    print("===================================\n")

    print_console(response_text)

    try:

        json_body = [

            {
                "measurement": "aiperf_ai_insights",

                "tags": {
                    "run_id": run_id,
                    "insight_type": "gpt_release_advisor"
                },

                "fields": {
                    "question": str(user_question),
                    "insight_text": str(response_text)
                }
            }

        ]

        client.write_points(json_body)

        print(
            "\nGPT RESPONSE WRITTEN TO INFLUXDB\n"
        )

    except Exception as ex:

        print(
            f"Unable to write GPT response: {str(ex)}"
        )
    finally:
        client.close()

    return response_text

# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    response = generate_ai_advice()

    print("\n===================================")
    print("FINAL AI RESPONSE")
    print("===================================\n")

    print_console(response)
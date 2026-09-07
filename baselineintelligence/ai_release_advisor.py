# =====================================================
# AiPERF GPT Copilot
# =====================================================

from dotenv import load_dotenv
from influxdb import InfluxDBClient

import requests
import os
import re
import sys
from datetime import datetime, timedelta, timezone

try:
    from .evidence_orchestrator import prepare_evidence
except ImportError:
    from evidence_orchestrator import prepare_evidence


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

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise Exception(
            f"Unexpected response from model: {data}"
        )

    message = choices[0].get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
        )
    if not isinstance(content, str) or not content.strip():
        raise Exception("Model returned an empty response")
    return content.strip()

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
        package = get_findings_package(client, requested_run_id)
        if package is not None:
            return requested_run_id, package
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


def get_findings_package(client, run_id):
    """Load one exact run package without silently substituting another run."""
    exact_query = f"""
    SELECT *
    FROM aiperf_findings_package
    WHERE run_id='{run_id}'
    ORDER BY time DESC
    LIMIT 1
    """
    exact_rows = list(client.query(exact_query).get_points())
    if not exact_rows:
        return None
    return exact_rows[0].get("findings_json", "No findings available.")


def get_run_catalog(client, date_text=None):
    """Return known findings runs, optionally restricted to a calendar date."""
    if date_text:
        day = datetime.strptime(date_text, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        start = day.strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (day + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = (
            'SELECT run_id FROM "aiperf_findings_package" '
            f"WHERE time >= '{start}' AND time < '{end}'"
        )
    else:
        query = 'SELECT run_id FROM "aiperf_findings_package"'

    rows = list(client.query(query).get_points())
    return sorted(
        {str(row["run_id"]) for row in rows if row.get("run_id")},
        reverse=True,
    )


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

def generate_ai_advice(user_question=None, requested_run_id=None):

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

    requested_run_id = requested_run_id or os.getenv("RUN_ID")
    date_match = re.search(
        r"\b(20\d{2}-\d{2}-\d{2})\b",
        user_question or "",
    )
    list_runs_requested = bool(re.search(
        r"\b(show|list|give|get)\b.*\b(run[\s_-]*ids?|runs?)\b",
        user_question or "",
        flags=re.IGNORECASE,
    ))
    report_requested = "comparison report" in (user_question or "").lower()
    catalog = get_run_catalog(
        client,
        date_text=date_match.group(1) if date_match else None,
    ) if list_runs_requested or date_match or report_requested else []
    run_ids = list(dict.fromkeys(re.findall(
        r"RUN_[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)+",
        user_question or "",
        flags=re.IGNORECASE,
    )))
    if len(run_ids) >= 2:
        run_id = run_ids[0]
        packages = []
        for comparison_run_id in run_ids[:2]:
            package = get_findings_package(client, comparison_run_id)
            try:
                package = json.loads(package) if package is not None else {}
            except (TypeError, ValueError):
                package = {"run_id": comparison_run_id, "executive_summary": str(package)}
            if isinstance(package, dict):
                package.setdefault("run_id", comparison_run_id)
            packages.append(package)
        findings_context = json.dumps(packages, default=str)
    else:
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

    # GPT receives the bounded evidence contract, never raw metric rows from
    # the persisted findings package.
    evidence_context = prepare_evidence(
        client, str(run_id), findings_context
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

    routing_question = user_question
    follow_up_marker = "Follow-up user question:"
    if follow_up_marker in routing_question:
        routing_question = routing_question.rsplit(follow_up_marker, 1)[1].strip()
    normalized_question = routing_question.lower()
    conceptual_question = (
        ("performance testing" in normalized_question
         or "load testing" in normalized_question
         or "stress testing" in normalized_question
         or "testing methodology" in normalized_question)
        and any(
            phrase in normalized_question
            for phrase in ("explain", "what is", "what are", "how does", "define")
        )
        and not any(
            term in normalized_question
            for term in (
                "run", "execution", "result", "metric", "latency", "p95",
                "p99", "regression", "baseline", "release",
            )
        )
    )
    performance_keywords = (
        "release", "readiness", "api", "latency", "throughput",
        "response", "p95", "p99", "baseline", "risk", "service", "bottleneck",
        "execution", "regression", "transaction", "memory", "cpu", "thread",
        "jvm", "anomaly", "capacity", "sla", "availability", "scalability",
        "reliability", "similar execution", "executive summary",
        "summarize this execution", "what happened", "investigate", "next step",
    )
    is_performance_question = any(
        keyword in normalized_question
        for keyword in performance_keywords
    ) or len(run_ids) >= 2 or bool(catalog)
    is_performance_question = is_performance_question and not conceptual_question

    if is_performance_question:
        intent = "release_readiness"
        if any(term in normalized_question for term in ("similar", "historical", "previous")):
            intent = "historical_comparison"
        elif any(term in normalized_question for term in ("root cause", "why", "investigate")):
            intent = "root_cause"
        prompt = f"""
You are AiPERF Copilot.

You are an expert Chief Performance Architect for an AI-native performance
engineering platform.

Answer the user's question using only the intent-specific evidence contract below.
Intent: {intent}
This is not a general document summarizer and must not ask the user to provide
another document when the package contains evidence.

RUN ID:
{run_id}

AIPERF EVIDENCE CONTRACT:
{json.dumps(evidence_context, indent=2, default=str)}

AVAILABLE RUN CATALOG:
{catalog if catalog else "Not requested."}

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

Use only the evidence available in the evidence contract. Do not request or
infer raw metrics that are not present.

Do not invent metrics.

For every important conclusion, name the relevant transaction or service and metric.
Distinguish observed evidence from inference. If evidence is missing, say so.
Respond in markdown with concise tables where they improve readability.
If the user asks for run IDs, list only IDs present in AVAILABLE RUN CATALOG.
If the user asks for a date, use the date-filtered catalog when available.
If the user asks for a comparison report, explain that the Jenkins-published
HTML report is generated for the latest pipeline comparison; for arbitrary
runs, provide the evidence comparison from the exact findings packages and
state when a published HTML artifact is not available.
"""
    else:
        prompt = f"""
You are AiPERF Copilot, a helpful and respectful assistant.

Respond naturally and respectfully to the user's message. If it is a
greeting, acknowledge it warmly. Do not force a performance analysis unless
the user asks about performance engineering or the execution.

USER MESSAGE:
{user_question}

Provide only the concise response.
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
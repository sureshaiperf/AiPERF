# ai_release_advisor.py

from langchain_core.language_models import LLM
from typing import List, Optional
from dotenv import load_dotenv
import requests
import os

# =====================================================
# Load Environment Variables
# =====================================================

load_dotenv(override=True)
config = os.environ

# =====================================================
# Custom LLM Wrapper
# =====================================================

class CustomLLM(LLM):

    model: str
    endpoint_url: str = config["API_URL"]

    headers: dict = {
        "Content-Type": "application/json",
        "X-API-KEY": config["API_KEY"]
    }

    temperature: float = 0.3
    top_p: float = 0.9
    max_tokens: int = 300

    def _call(self, prompt: str,
              stop: Optional[List[str]] = None) -> str:

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens
        }

        response = requests.post(
            self.endpoint_url,
            headers=self.headers,
            json=payload
        )

        response.raise_for_status()

        data = response.json()

        return data["choices"][0]["message"]["content"]

    @property
    def _llm_type(self):
        return "custom_llm"

# =====================================================
# AI Release Advisor
# =====================================================

def generate_ai_advice():

    # TEMP TEST VALUES
    current_run = "RUN_010"
    similar_run = "RUN_007"

    similarity_score = 91
    p95_change = 18
    throughput_change = -12
    gateway_cpu_change = 22

    prompt = f"""
You are an AiPERF Release Advisor.

Current Run: {current_run}
Most Similar Run: {similar_run}
Similarity Score: {similarity_score}%

Observations:
- P95 response time increased by {p95_change}%
- Throughput decreased by {abs(throughput_change)}%
- Gateway CPU utilization increased by {gateway_cpu_change}%

Provide:

1. Release Risk
2. Root Cause Hypothesis
3. Recommendation

Keep the response below 100 words.
"""

    llm = CustomLLM(
        model="gpt-5-chat",
        temperature=0.3,
        top_p=0.9,
        max_tokens=300
    )

    print("\n===================================")
    print(" AIPERF AI RELEASE ADVISOR")
    print("===================================\n")

    response = llm.invoke(prompt)

    print(response)

# =====================================================
# Main
# =====================================================

if __name__ == "__main__":
    generate_ai_advice()
# =====================================================
# AiPERF Copilot
# =====================================================

import streamlit as st
from influxdb import InfluxDBClient

# =====================================================
# PAGE CONFIG
# =====================================================

st.set_page_config(
    page_title="AiPERF Copilot",
    page_icon="🚀",
    layout="wide"
)

# =====================================================
# INFLUXDB
# =====================================================

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="jmeter"
)

# =====================================================
# UI HEADER
# =====================================================

st.title("AiPERF Copilot")

st.caption(
    "AI-Native Performance Engineering Intelligence Platform"
)

# =====================================================
# SIDEBAR
# =====================================================

with st.sidebar:

    st.header("AiPERF")

    st.write("✅ Transaction Comparison")
    st.write("✅ Service Comparison")
    st.write("✅ Variance Ranking")
    st.write("✅ RCA Engine")
    st.write("✅ GPT Release Advisor")

    st.divider()

    st.write("TechCon 2026 Demo")

# =====================================================
# USER INPUT
# =====================================================

question = st.text_input(
    "Ask AiPERF",
    placeholder="Can I release this build?"
)

# =====================================================
# HELPER
# =====================================================

def extract_section(text, section_name):

    try:

        start = text.find(section_name)

        if start == -1:
            return ""

        next_sections = [
            "Executive Summary:",
            "Risk Level:",
            "Release Recommendation:",
            "Root Cause Hypothesis:",
            "Recommended Actions:"
        ]

        end_positions = []

        for section in next_sections:

            pos = text.find(
                section,
                start + len(section_name)
            )

            if pos > start:
                end_positions.append(pos)

        end = min(end_positions) if end_positions else len(text)

        value = text[
            start + len(section_name):end
        ].strip()

        return value

    except Exception:

        return ""

# =====================================================
# ASK BUTTON
# =====================================================

if st.button("Ask"):

    try:

        query = """
        SELECT *
        FROM aiperf_ai_insights
        WHERE insight_type='gpt_release_advisor'
        ORDER BY time DESC
        LIMIT 1
        """

        rows = list(
            client.query(query).get_points()
        )

        if not rows:

            st.warning(
                "No GPT Release Advisor data found."
            )

        else:

            result = rows[0]

            run_id = result.get(
                "run_id",
                "UNKNOWN"
            )

            insight_text = result.get(
                "insight_text",
                ""
            )

            insight_text = insight_text.replace(
                "\\n",
                "\n"
            )

            # =========================================
            # PARSE RESPONSE
            # =========================================

            executive_summary = extract_section(
                insight_text,
                "Executive Summary:"
            )

            risk_level = extract_section(
                insight_text,
                "Risk Level:"
            )

            release_recommendation = extract_section(
                insight_text,
                "Release Recommendation:"
            )

            root_cause = extract_section(
                insight_text,
                "Root Cause Hypothesis:"
            )

            recommended_actions = extract_section(
                insight_text,
                "Recommended Actions:"
            )

            # =========================================
            # RELEASE DECISION BANNER
            # =========================================

            recommendation_upper = (
                release_recommendation.upper().strip()
            )

            if "NO GO" in recommendation_upper:

                st.error(
                    "🚫 RELEASE DECISION : NO GO"
                )

            elif "GO WITH CAUTION" in recommendation_upper:

                st.warning(
                    "⚠️ RELEASE DECISION : GO WITH CAUTION"
                )

            elif recommendation_upper == "GO":

                st.success(
                    "✅ RELEASE DECISION : GO"
                )

            else:

                st.info(
                    f"ℹ️ RELEASE DECISION : {release_recommendation}"
                )

            # =========================================
            # HEADER
            # =========================================

            st.success(
                f"Latest Run : {run_id}"
            )

            # =========================================
            # METRICS
            # =========================================

            col1, col2 = st.columns(2)

            with col1:

                st.metric(
                    "Risk Level",
                    risk_level
                )

            with col2:

                st.metric(
                    "Release Recommendation",
                    release_recommendation
                )

            st.divider()

            # =========================================
            # EXEC SUMMARY
            # =========================================

            st.subheader(
                "Executive Summary"
            )

            st.write(
                executive_summary
            )

            st.divider()

            # =========================================
            # RCA
            # =========================================

            st.subheader(
                "Root Cause Hypothesis"
            )

            st.write(
                root_cause
            )

            st.divider()

            # =========================================
            # ACTIONS
            # =========================================

            st.subheader(
                "Recommended Actions"
            )

            st.write(
                recommended_actions
            )

            with st.expander(
                "View Full GPT Response"
            ):
                st.text(
                    insight_text
                )

    except Exception as ex:

        st.error(
            f"Error: {str(ex)}"
        )
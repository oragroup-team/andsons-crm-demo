"""Insight agent - turns a business signal ("OTC serum sales are down, write
something to fix it") into a grounded brief the Copywriter can act on.

Deliberately reuses ask_analytics() rather than querying BigQuery directly:
every guardrail already built for the Analytics Chat (write-blocking,
PII-blocking, number-verification against actual query results) applies for
free here too, instead of a second, less-hardened path to the same
warehouse.

MoEngage (campaign/engagement data) is optional context, summarized by an
LLM call that is explicitly instructed to describe only what's in the real
API response - never invent a metric that isn't there. If MoEngage isn't
configured, that's stated plainly rather than silently omitted, so nobody
mistakes "not connected" for "nothing interesting going on".

The brief text handed to the Copywriter is framed as STRATEGY CONTEXT, not
customer-facing content: generate_email() is responsible for making sure no
raw number, table name, or "engagement dropped 12%" style internal metric
ever leaks into the email itself.
"""
import logging

from langchain_core.prompts import ChatPromptTemplate

import moengage_client
from .analytics_agent import ask_analytics
from .llm_provider import get_llm

logger = logging.getLogger("insight_agent")

_MOENGAGE_SUMMARY_PROMPT = """You are summarizing one real MoEngage analytics chart's raw API response \
for an internal marketing brief. Describe ONLY what is actually present in this JSON - if a number, \
trend, or breakdown isn't in the data, do not mention it or guess at it. Two to four short sentences, \
plain English, no code/JSON formatting in your answer.

Chart label: {label}
Raw chart data:
{data}
"""


def _summarize_moengage_snapshot(label: str, data: dict) -> str:
    llm = get_llm("ANALYTICS")
    prompt = ChatPromptTemplate.from_messages([("human", _MOENGAGE_SUMMARY_PROMPT)])
    chain = prompt | llm
    result = chain.invoke({"label": label, "data": str(data)[:6000]})
    return result.content.strip() if hasattr(result, "content") else str(result).strip()


def _gather_moengage_notes() -> str:
    if not moengage_client.is_configured():
        return "MoEngage is not connected yet - this brief is based on BigQuery sales data only."

    snapshots = moengage_client.get_configured_chart_snapshots()
    if not snapshots:
        return (
            "MoEngage is connected but no charts are configured (set MOENGAGE_CHARTS) - this brief is "
            "based on BigQuery sales data only."
        )

    notes = []
    for snap in snapshots:
        if snap["error"]:
            notes.append(f"- {snap['label']}: unavailable ({snap['error']})")
        else:
            try:
                notes.append(f"- {snap['label']}: {_summarize_moengage_snapshot(snap['label'], snap['data'])}")
            except Exception as exc:  # noqa: BLE001 - one bad summary shouldn't kill the brief
                logger.warning("Failed to summarize MoEngage chart %r: %s", snap["label"], exc)
                notes.append(f"- {snap['label']}: could not summarize (see logs)")
    return "MoEngage campaign/engagement context:\n" + "\n".join(notes)


def investigate(question: str, file_context: str = "") -> dict:
    """Investigate a business signal using real BigQuery data (always) and
    real MoEngage data (if configured). Returns a dict with the verified
    BigQuery answer/SQL for transparency, plus `brief_text` - the combined,
    clearly-labeled context to hand to generate_email()."""
    bigquery_question = (
        f"{question}\n\nQuantify this with real numbers: show the actual trend or comparison "
        "(e.g. recent period vs. the prior one) so it's clear whether this is really happening and by "
        "how much, broken down by whatever's most relevant (product, channel, flow) if the data supports it."
    )
    bq_result = ask_analytics(bigquery_question)

    moengage_notes = _gather_moengage_notes()

    sections = [
        "BUSINESS SIGNAL RAISED: " + question,
        "",
        "REAL BIGQUERY FINDING (verified against live data):",
        bq_result["answer"],
    ]
    if not bq_result.get("verified"):
        sections.append(
            "(Note: this finding could not be fully verified against query results - treat it as "
            "directional only, not a hard number.)"
        )
    sections += ["", moengage_notes]

    if file_context:
        sections += ["", "DATA FROM A FILE UPLOADED WITH THIS REQUEST:", file_context]

    return {
        "brief_text": "\n".join(sections),
        "bigquery_answer": bq_result["answer"],
        "bigquery_sql": bq_result["sql_query"],
        "bigquery_verified": bq_result.get("verified", False),
        "moengage_configured": moengage_client.is_configured(),
    }

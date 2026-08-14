"""Insight agent - turns a business signal ("OTC serum sales are down, write
something to fix it") into a grounded brief the Copywriter can act on.

Deliberately reuses ask_analytics() rather than querying BigQuery directly:
every guardrail already built for the Analytics Chat (write-blocking,
PII-blocking, number-verification against actual query results) applies for
free here too, instead of a second, less-hardened path to the same
warehouse.

MoEngage (campaign/engagement data) is optional context, pulled from EVERY
chart on EVERY dashboard in the workspace (moengage_client.get_all_chart_snapshots
- ~138 charts across the real andSons workspace) and reduced to one LLM
summarization call that's told which charts are actually relevant to this
question and to describe only what's really in the data - never invent a
metric that isn't there. If MoEngage isn't configured, that's stated
plainly rather than silently omitted, so nobody mistakes "not connected"
for "nothing interesting going on".

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

_MOENGAGE_SUMMARY_PROMPT = """You are given the REAL raw data from every chart on every MoEngage \
analytics dashboard in this workspace ({chart_count} charts total), for an internal marketing brief. \
The business question motivating this brief is:
{question}

Pick out and summarize ONLY the charts whose data is actually relevant to that question (e.g. a \
question about winback pulls from the Winback charts, not the ED/Weight-Loss funnel charts - this \
workspace covers multiple andSons programs, so most charts will be irrelevant to any one question - \
skip them). Describe ONLY what is actually present in the data you use - never invent or estimate a \
number, trend, or breakdown that isn't really there, and never mention a chart you're not actually \
using. If genuinely nothing here is relevant to the question, say so plainly instead of forcing a \
connection. Three to six short sentences, plain English, no code/JSON formatting, no markdown.

Charts (label: raw data):
{charts}
"""

# Cap per-chart data in the summarization prompt so ~138 charts' worth of
# real payloads stays within a sane prompt size - full data is still fetched
# and available (get_all_chart_snapshots), this cap only bounds what goes
# into this one summarization call.
_PER_CHART_CHAR_CAP = 800


def _summarize_all_snapshots(question: str, snapshots: list) -> str:
    lines = []
    for snap in snapshots:
        if snap["error"]:
            lines.append(f"- {snap['label']}: [unavailable - {snap['error']}]")
        else:
            lines.append(f"- {snap['label']}: {str(snap['data'])[:_PER_CHART_CHAR_CAP]}")

    llm = get_llm("ANALYTICS")
    prompt = ChatPromptTemplate.from_messages([("human", _MOENGAGE_SUMMARY_PROMPT)])
    chain = prompt | llm
    result = chain.invoke(
        {"question": question, "chart_count": len(snapshots), "charts": "\n".join(lines)}
    )
    return result.content.strip() if hasattr(result, "content") else str(result).strip()


def _gather_moengage_notes(question: str) -> str:
    if not moengage_client.is_configured():
        return "MoEngage is not connected yet - this brief is based on BigQuery sales data only."

    snapshots = moengage_client.get_all_chart_snapshots()
    if not snapshots:
        return (
            "MoEngage is connected but has no dashboards/charts yet - this brief is based on BigQuery "
            "sales data only."
        )

    failed = [s for s in snapshots if s["error"]]
    try:
        summary = _summarize_all_snapshots(question, snapshots)
    except Exception as exc:  # noqa: BLE001 - a summarization failure shouldn't kill the whole brief
        logger.warning("Failed to summarize MoEngage snapshots: %s", exc)
        return f"MoEngage is connected ({len(snapshots)} charts pulled) but the summary step failed - this brief is based on BigQuery sales data only."

    notes = f"MoEngage campaign/engagement context ({len(snapshots)} charts checked):\n{summary}"
    if failed:
        notes += f"\n({len(failed)} chart(s) could not be fetched and were excluded.)"
    return notes


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

    moengage_notes = _gather_moengage_notes(question)

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

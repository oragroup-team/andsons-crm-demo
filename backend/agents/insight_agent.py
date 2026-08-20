"""Insight agent - turns a business signal ("OTC serum sales are down, write
something to fix it") into a grounded brief the Copywriter can act on.

Deliberately reuses ask_analytics() rather than querying BigQuery directly:
every guardrail already built for the Analytics Chat (write-blocking,
PII-blocking, number-verification against actual query results) applies for
free here too, instead of a second, less-hardened path to the same
warehouse.

MoEngage (campaign/engagement data) is optional context (moengage_summary.py,
shared with the Analytics Chat) - a cheap relevance pre-check first, then
(only if that says yes) every chart on every dashboard in the workspace
(~138 charts) reduced to one LLM summarization call, told which charts are
actually relevant to this question and to describe only what's really in
the data - never invent a metric that isn't there. If MoEngage isn't
configured, or isn't relevant to this signal, that's stated plainly rather
than silently omitted, so nobody mistakes "not connected"/"not relevant"
for "nothing interesting going on".

The brief text handed to the Copywriter is framed as STRATEGY CONTEXT, not
customer-facing content: generate_email() is responsible for making sure no
raw number, table name, or "engagement dropped 12%" style internal metric
ever leaks into the email itself.
"""
import moengage_client
from moengage_summary import gather_moengage_context

from .analytics_agent import ask_analytics
from .llm_provider import get_llm


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

    moengage_notes, moengage_relevant, _, _ = gather_moengage_context(question, get_llm("ANALYTICS"))
    if not moengage_relevant:
        moengage_notes += " This brief is based on BigQuery sales data only."

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

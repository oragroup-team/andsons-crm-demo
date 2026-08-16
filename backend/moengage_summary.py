"""Shared MoEngage chart summarization - turns the full chart snapshot set
(moengage_client.get_all_chart_snapshots, ~138 charts across the real
andSons workspace) into a plain-English summary scoped to one specific
question, reduced to a single LLM call rather than one call per chart.

Used by both agents.insight_agent (email strategy briefs) and
agents.analytics_agent (the Analytics Chat) - one hardened implementation
instead of two independently-guessed ones, same reasoning as insight_agent
reusing ask_analytics() for BigQuery rather than a second query path.

Two-stage, deliberately: a full chart fetch+summarize costs ~40-50s (138
charts). Most analytics questions ("how many orders") have nothing to do
with campaign/engagement data at all, so a cheap pre-check (~1-2s) decides
whether it's even plausibly relevant before paying that cost - found live:
without this, a pure order-count question was taking 50s and MoEngage was
even claiming false relevance by prose-matching alone. Both the pre-check
and the final relevance call use structured output (a boolean field), not
prose-prefix matching, for exactly that reason - a model saying "none of
this is relevant" in the summary text itself is not reliably distinguishable
from a real finding by string matching.
"""
import logging
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

import moengage_client

logger = logging.getLogger("moengage_summary")

# Literal["yes", "no"] rather than a raw bool field: caught live, Groq's
# tool-calling occasionally emits a bare boolean as the JSON STRING "false"
# instead of the JSON literal false, which then fails Groq's own strict
# schema validation server-side before the call even completes (a Pydantic-
# level coercion can't help - the request itself gets rejected). A string
# enum field doesn't have a type to mismatch, so it isn't exposed to this
# failure mode the same way.
_YesNo = Literal["yes", "no"]


class _NeedsMoEngage(BaseModel):
    needs_moengage: _YesNo = Field(
        description="'yes' if answering this question plausibly requires MoEngage campaign/engagement "
        "data (email opens, clicks, delivery rates, funnel drop-off, flow performance). 'no' if it's "
        "purely about orders, revenue, marketing spend, or customer counts from the sales database - "
        "that data lives in BigQuery, not MoEngage."
    )


class _MoEngageSummary(BaseModel):
    relevant: _YesNo = Field(
        description="'yes' only if at least one real chart's data actually answers or informs this "
        "question. 'no' if you checked and genuinely nothing here applies - do not force a connection."
    )
    summary: str = Field(
        description="If relevant='yes': three to six short plain-English sentences describing ONLY what "
        "is actually present in the charts you used - never invent or estimate a number/trend that isn't "
        "really there, never mention a chart you're not using. If relevant='no': empty string."
    )


_PRECHECK_PROMPT = "Question: {question}"

_MOENGAGE_SUMMARY_PROMPT = """You are given the REAL raw data from every chart on every MoEngage \
analytics dashboard in this workspace ({chart_count} charts total). The question motivating this is:
{question}

This workspace covers multiple andSons programs (hair loss, ED, weight loss, etc.), so most charts \
will be irrelevant to any one question - pick out ONLY the ones whose data actually applies.

Charts (label: raw data):
{charts}
"""

# Cap per-chart data in the summarization prompt so ~138 charts' worth of
# real payloads stays within a sane prompt size - full data is still fetched
# and available (get_all_chart_snapshots), this cap only bounds what goes
# into this one summarization call.
_PER_CHART_CHAR_CAP = 800


def _might_need_moengage(question: str, llm) -> bool:
    structured_llm = llm.with_structured_output(_NeedsMoEngage)
    prompt = ChatPromptTemplate.from_messages([("human", _PRECHECK_PROMPT)])
    chain = prompt | structured_llm
    try:
        result: _NeedsMoEngage = chain.invoke({"question": question})
        return result.needs_moengage == "yes"
    except Exception as exc:  # noqa: BLE001 - fail safe to "skip", BigQuery-only is still a complete answer
        logger.warning("MoEngage relevance pre-check failed for %r: %s", question, exc)
        return False


def _summarize_all_snapshots(question: str, snapshots: list, llm) -> _MoEngageSummary:
    lines = []
    for snap in snapshots:
        if snap["error"]:
            lines.append(f"- {snap['label']}: [unavailable - {snap['error']}]")
        else:
            lines.append(f"- {snap['label']}: {str(snap['data'])[:_PER_CHART_CHAR_CAP]}")

    structured_llm = llm.with_structured_output(_MoEngageSummary)
    prompt = ChatPromptTemplate.from_messages([("human", _MOENGAGE_SUMMARY_PROMPT)])
    chain = prompt | structured_llm
    return chain.invoke({"question": question, "chart_count": len(snapshots), "charts": "\n".join(lines)})


def gather_moengage_context(question: str, llm) -> tuple:
    """Returns (text, relevant). `relevant` is the only signal callers
    should use to decide whether real MoEngage data actually informed the
    answer - never infer it from `text`'s wording, which exists purely for
    display/context and can describe a "nothing relevant" outcome in
    several different ways."""
    if not moengage_client.is_configured():
        return "MoEngage is not connected.", False

    if not _might_need_moengage(question, llm):
        return "MoEngage was not checked - this question doesn't look like it needs campaign/engagement data.", False

    try:
        snapshots = moengage_client.get_all_chart_snapshots()
    except Exception as exc:  # noqa: BLE001 - report, don't propagate
        logger.warning("Failed to fetch MoEngage chart snapshots: %s", exc)
        return f"MoEngage is connected but the chart fetch failed ({exc}).", False

    if not snapshots:
        return "MoEngage is connected but has no dashboards/charts yet.", False

    failed = [s for s in snapshots if s["error"]]
    try:
        result = _summarize_all_snapshots(question, snapshots, llm)
    except Exception as exc:  # noqa: BLE001 - a summarization failure shouldn't kill the caller
        logger.warning("Failed to summarize MoEngage snapshots: %s", exc)
        return f"MoEngage is connected ({len(snapshots)} charts pulled) but the summary step failed.", False

    if result.relevant != "yes":
        return f"Checked all {len(snapshots)} MoEngage charts - none are relevant to this question.", False

    notes = f"MoEngage campaign/engagement data ({len(snapshots)} charts checked):\n{result.summary}"
    if failed:
        notes += f"\n({len(failed)} chart(s) could not be fetched and were excluded.)"
    return notes, True

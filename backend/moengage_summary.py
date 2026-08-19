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
        "really there, never mention a chart you're not using. If relevant='no': empty string. Write this "
        "as a finished answer a customer-facing analyst would say out loud, not as a description of the "
        "data source: never say 'chart', 'dashboard', 'data shows', or name a raw metric/field label - "
        "translate every one into the plain business term (e.g. a chart tracking step-1-to-step-2 dropoff "
        "on a winback flow becomes 'winback emails that get a response', not a description of the chart). "
        "This may be used directly as someone's final answer with no further editing, so it must already "
        "read like one: lead with the headline finding, plain and confident, zero trace it came from a "
        "chart at all."
    )


class _MoEngageFinding(BaseModel):
    summary: str = Field(
        description="Three to six short plain-English sentences reporting what the REAL chart data below "
        "actually says about this question's engagement-metric angle (opens, clicks, delivery, funnel, "
        "engagement, flow performance) - this question already contains an explicit reference to one of "
        "these, so genuinely search for it across the charts rather than defaulting to 'not available'. "
        "Never invent or estimate a number/trend that isn't really there. Only if, after actually looking, "
        "truly nothing in these charts covers it, say that plainly in one honest sentence instead - but "
        "only after really checking, not as a default. Write this as a finished answer a customer-facing "
        "analyst would say out loud: never say 'chart', 'dashboard', 'data shows', or name a raw metric/"
        "field label - translate every one into the plain business term. This may be used directly as "
        "someone's final answer with no further editing."
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

_MOENGAGE_FINDING_PROMPT = """You are given the REAL raw data from every chart on every MoEngage analytics \
dashboard in this workspace ({chart_count} charts total). This question already contains an explicit, real \
engagement-metric reference (opens, clicks, delivery, funnel, engagement, or flow performance) - it is \
already known this needs MoEngage data, so your only job is to find and report what the real data actually \
says, not to decide whether to look. The question motivating this is:
{question}

This workspace covers multiple andSons programs (hair loss, ED, weight loss, etc.) and this question may \
also have parts about revenue or orders that these charts don't cover - that's fine, just report the real \
engagement-metric part genuinely, searching properly across every chart below before concluding nothing \
applies.

Charts (label: raw data):
{charts}
"""

# Cap per-chart data in the summarization prompt so ~138 charts' worth of
# real payloads stays within a sane prompt size - full data is still fetched
# and available (get_all_chart_snapshots), this cap only bounds what goes
# into this one summarization call.
_PER_CHART_CHAR_CAP = 800


# Caught live: the LLM pre-check alone is genuinely non-deterministic - the
# exact same question got "yes" and "no" seconds apart across repeated
# calls, which is unacceptable for a binary "should we even look" gate (a
# false negative here means silently answering from the wrong data source,
# not just a style difference). A keyword match on unambiguous
# MoEngage-only terms can't flake the way a model sample can, so it's
# checked FIRST and short-circuits straight to "yes" - the LLM call is a
# secondary catch-all for phrasing these keywords miss (e.g. "how well is
# winback landing"), not the only line of defense for the obvious cases.
_MOENGAGE_KEYWORDS = (
    "open rate", "opens", "click rate", "click-through", "clickthrough", "ctr",
    "delivery rate", "delivered rate", "deliver rate",
    "engagement", "funnel", "drop-off", "dropoff", "drop off",
    "moengage", "campaign performance", "email performance", "flow performance",
)


def _might_need_moengage(question: str, llm) -> tuple:
    """Returns (might_need, keyword_matched). keyword_matched - a real,
    unambiguous MoEngage-only term (e.g. 'opens', 'funnel') was present
    verbatim in the question - is exposed separately from the overall
    boolean because it feeds a real downstream fix: the SEPARATE relevance
    judgment inside _summarize_all_snapshots is its own independent LLM
    call and has its own documented flakiness (the exact same question
    getting 'yes' and 'no' seconds apart) - when the question already
    contains an explicit, unambiguous engagement term, that deterministic
    signal should carry more weight against that second judgment's flakiness
    than an LLM-only precheck would."""
    q_lower = question.lower()
    if any(keyword in q_lower for keyword in _MOENGAGE_KEYWORDS):
        return True, True

    structured_llm = llm.with_structured_output(_NeedsMoEngage)
    prompt = ChatPromptTemplate.from_messages([("human", _PRECHECK_PROMPT)])
    chain = prompt | structured_llm
    try:
        result: _NeedsMoEngage = _invoke_with_retry(
            chain, {"question": question}, attempts=3, label="MoEngage relevance pre-check",
        )
        return result.needs_moengage == "yes", False
    except Exception as exc:  # noqa: BLE001 - fail safe to "skip", BigQuery-only is still a complete answer
        logger.warning("MoEngage relevance pre-check failed for %r: %s", question, exc)
        return False, False


def _invoke_with_retry(chain, payload: dict, attempts: int = 5, label: str = "MoEngage LLM call"):
    """Local retry wrapper - the SAME real, documented Groq failure mode
    already fixed everywhere else in this codebase via
    llm_provider.invoke_with_retry (forced tool-calling mode rejects the
    call outright, 'Tool choice is required, but model did not call a
    tool', when the model tries to answer in free text instead of the
    required structured schema), but this file's calls need real per-
    invocation template variables filled (question/chart data), which
    invoke_with_retry's own hardcoded chain.invoke({}) doesn't support -
    so a small local equivalent instead of forcing this file's calls to
    fit that signature. Real, live-caught bug this fixes: EVERY attempt in
    the outer relevance-retry loop below was failing with this exact 400
    error on the same nuanced compound question, consistently, across
    multiple separate live requests - not sampling noise, a genuine
    missing-retry gap this file had that every other LLM call site in this
    codebase already closed. Raises the last exception if every attempt
    fails - the caller already handles that."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return chain.invoke(payload)
        except Exception as exc:  # noqa: BLE001 - every attempt logged, caller decides final handling
            last_exc = exc
            logger.warning("%s failed (attempt %d/%d): %s", label, attempt + 1, attempts, exc)
    raise last_exc


def _summarize_all_snapshots(question: str, snapshots: list, llm, keyword_matched: bool = False) -> _MoEngageSummary:
    """keyword_matched=True uses a genuinely DIFFERENT, ungated call, not
    just a stronger hint on the same one - the normal relevant='yes'/'no'
    gate asks the model to re-decide something this codebase already
    deterministically knows (the keyword match itself proves engagement
    data is being asked about), which is pure unnecessary risk once a real
    per-call retry (below) is handling the actual raw-failure case; skip
    that gate entirely for this case and only ask it to find and report
    the real data, using _MoEngageFinding (no relevant field to flake on)."""
    lines = []
    for snap in snapshots:
        if snap["error"]:
            lines.append(f"- {snap['label']}: [unavailable - {snap['error']}]")
        else:
            lines.append(f"- {snap['label']}: {str(snap['data'])[:_PER_CHART_CHAR_CAP]}")
    charts_text = "\n".join(lines)

    if keyword_matched:
        structured_llm = llm.with_structured_output(_MoEngageFinding)
        prompt = ChatPromptTemplate.from_messages([("human", _MOENGAGE_FINDING_PROMPT)])
        chain = prompt | structured_llm
        finding: _MoEngageFinding = _invoke_with_retry(
            chain, {"question": question, "chart_count": len(snapshots), "charts": charts_text},
            label="MoEngage finding call",
        )
        return _MoEngageSummary(relevant="yes", summary=finding.summary)

    structured_llm = llm.with_structured_output(_MoEngageSummary)
    prompt = ChatPromptTemplate.from_messages([("human", _MOENGAGE_SUMMARY_PROMPT)])
    chain = prompt | structured_llm
    return _invoke_with_retry(
        chain, {"question": question, "chart_count": len(snapshots), "charts": charts_text},
        label="MoEngage summary call",
    )


def gather_moengage_context(question: str, llm) -> tuple:
    """Returns (text, relevant, raw_summary). `relevant` is the only signal
    callers should use to decide whether real MoEngage data actually
    informed the answer - never infer it from `text`'s wording, which
    exists purely for display/context (it names chart counts, deliberately,
    for a downstream agent's own transparency) and can describe a "nothing
    relevant" outcome in several different ways. `raw_summary` is the plain,
    customer-voiced finding alone (empty string if not relevant) - safe to
    use directly as a final answer with no further editing (unlike `text`,
    it never mentions charts/dashboards - see _MoEngageSummary.summary's
    own docstring), for a caller that determined MoEngage is the ONLY
    source this question needs and there is nothing to hand off to another
    agent to translate."""
    if not moengage_client.is_configured():
        return "MoEngage is not connected.", False, ""

    might_need, keyword_matched = _might_need_moengage(question, llm)
    if not might_need:
        return "MoEngage was not checked - this question doesn't look like it needs campaign/engagement data.", False, ""

    try:
        snapshots = moengage_client.get_all_chart_snapshots()
    except Exception as exc:  # noqa: BLE001 - report, don't propagate
        logger.warning("Failed to fetch MoEngage chart snapshots: %s", exc)
        return f"MoEngage is connected but the chart fetch failed ({exc}).", False, ""

    if not snapshots:
        return "MoEngage is connected but has no dashboards/charts yet.", False, ""

    failed = [s for s in snapshots if s["error"]]
    # Up to 2 tries normally (3 when the question already contains an
    # unambiguous engagement term - see keyword_matched): caught live, this
    # specific judgment ("is any chart actually relevant") is genuinely
    # non-deterministic - the identical question got "yes" and "no" seconds
    # apart across repeated real calls, and a live-caught case showed this
    # can still land on "no" on both of only 2 tries for a compound question
    # (real revenue+opens question, false "no" both attempts, when a
    # standalone opens-only question moments earlier correctly got "yes" on
    # the exact same underlying charts). We've already paid the full fetch
    # cost by this point, and a real, explicit engagement term in the
    # question makes a genuine "no" far less plausible than an LLM judgment
    # flake, so it's worth the extra attempt before trusting a "no".
    attempts = 3 if keyword_matched else 2
    result = None
    for attempt in range(attempts):
        try:
            result = _summarize_all_snapshots(question, snapshots, llm, keyword_matched=keyword_matched)
        except Exception as exc:  # noqa: BLE001 - a summarization failure shouldn't kill the caller
            logger.warning("Failed to summarize MoEngage snapshots (attempt %d): %s", attempt, exc)
            continue
        if result.relevant == "yes":
            break

    if result is None:
        return f"MoEngage is connected ({len(snapshots)} charts pulled) but the summary step failed.", False, ""
    if result.relevant != "yes":
        return f"Checked all {len(snapshots)} MoEngage charts - none are relevant to this question.", False, ""

    notes = f"MoEngage campaign/engagement data ({len(snapshots)} charts checked):\n{result.summary}"
    if failed:
        notes += f"\n({len(failed)} chart(s) could not be fetched and were excluded.)"
    return notes, True, result.summary

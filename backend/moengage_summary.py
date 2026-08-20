"""Shared MoEngage retrieval - real reasoning over the real MoEngage
"schema" (its catalog of dashboards/charts), not a keyword shortcut: MoEngage
has no free-form query capability at all (a real platform constraint - every
real answer has to come from a chart someone already built in the MoEngage
UI, see moengage_client.py's module docstring), so the direct analogy to a
SQL agent's schema-inspect -> write query -> execute -> answer loop is:
read the real chart catalog (names only, cheap) -> reason about which real
chart(s) genuinely help answer THIS question, grounded in what each one's
own name says it tracks -> fetch only those charts' real data -> validate
(does the real data actually answer it, once you actually look) -> narrate.

Used by both agents.insight_agent (email strategy briefs) and
agents.analytics_agent (the Analytics Chat) - one hardened implementation
instead of two independently-guessed ones, same reasoning as insight_agent
reusing ask_analytics() for BigQuery rather than a second query path.

No keyword list anywhere in this file, deliberately - an earlier version of
this module short-circuited relevance on a fixed list of "obviously
MoEngage" phrases (opens, funnel, etc.), which is exactly the kind of
hardcoded rule this codebase's own standing principle rejects everywhere
else: the model should understand the actual question and the actual
schema, not pattern-match on wording. It also broke in practice - real
BI-lead-authored business questions (unsubscribe rate, list health,
incremental revenue vs control group) used phrasing the list never
anticipated, and one of them slipped through silently claiming data
"wasn't available" that had simply never been checked. Confirmed live
(moengage_client.list_chart_catalog costs ~7s vs ~40-50s for a full data
fetch) that reasoning over the real catalog on every question is cheap
enough to not need a keyword gate to avoid paying for it at all.
"""
import logging
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

import moengage_client

logger = logging.getLogger("moengage_summary")

# Cap per-chart data in prompts so a chart with a very large raw payload
# doesn't blow out the prompt on its own - full data is still fetched, this
# only bounds what goes into the LLM call. Scales DOWN with how many charts
# were actually selected, not a flat constant - real gap this fixes: the
# old flat 800-char cap was sized for the PREVIOUS design (all ~138 charts
# in one prompt, so each one had to be tiny) and silently carried over into
# this one even though a genuine selection is normally a handful of charts,
# not 138 - a real chart's data (confirmed live: 17KB+ for a single
# rollout-monitoring chart) was being cut down to ~2 rows, discarding the
# very rows a before/after comparison actually needs. A generous total
# prompt budget, divided across however many charts were actually
# selected, uses the real headroom this design has now instead of a
# constant left over from the old one.
_TOTAL_CHART_DATA_BUDGET = 60000


def _per_chart_char_cap(num_charts: int) -> int:
    return max(2000, _TOTAL_CHART_DATA_BUDGET // max(num_charts, 1))


def _invoke_with_retry(chain, payload: dict, attempts: int = 5, label: str = "MoEngage LLM call"):
    """Real, documented Groq failure mode, same one llm_provider.
    invoke_with_retry already fixes everywhere else in this codebase
    (forced tool-calling mode rejects the call outright, 'Tool choice is
    required, but model did not call a tool', when the model tries to
    answer in free text instead of the required structured schema) - this
    file's calls need real per-invocation template variables filled
    (question/catalog/chart data), which invoke_with_retry's own hardcoded
    chain.invoke({}) doesn't support, so a small local equivalent instead
    of forcing this file's calls to fit that signature. Real, live-caught
    bug this fixes: a nuanced compound question failed with this exact 400
    error on every attempt of an earlier 2-3-attempt loop, consistently,
    across multiple separate live requests - not sampling noise, a genuine
    missing-retry gap. Raises the last exception if every attempt fails -
    callers already handle that."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return chain.invoke(payload)
        except Exception as exc:  # noqa: BLE001 - every attempt logged, caller decides final handling
            last_exc = exc
            logger.warning("%s failed (attempt %d/%d): %s", label, attempt + 1, attempts, exc)
    raise last_exc


class _ChartSelection(BaseModel):
    chart_labels: List[str] = Field(
        description="Every real chart label (exact 'dashboard name :: chart name' text, copied verbatim "
        "from the catalog below - never invent, abbreviate, or paraphrase one) that could plausibly help "
        "answer this question, based on genuinely understanding what each chart's own name says it tracks. "
        "Empty list if, after actually reading the real catalog, nothing plausibly applies - do not force a "
        "connection just to return something, and do not select a chart just because its topic sounds "
        "vaguely related if it doesn't actually track what's being asked."
    )
    reasoning: str = Field(description="One or two sentences: your real reasoning for these charts (or for none).")


_CATALOG_SELECTION_PROMPT = """Below is the REAL, COMPLETE catalog of every chart on every MoEngage \
analytics dashboard in this andSons workspace ({chart_count} charts total). This IS the real schema: \
MoEngage has no free-form query capability - every real answer has to come from one of these specific, \
already-built charts, a real platform constraint, not a limitation of this tool. Read the real question \
below, then genuinely reason about which of these real charts, if any, would actually help answer it, based \
on what each chart's own name tells you it tracks - not a keyword match on the question's wording, and \
not a guess about what MoEngage probably has somewhere.

Reason about what KIND of measurement a dashboard/chart name implies, not just literal word overlap with \
the question - a real analyst reading a chart catalog infers intent from naming conventions, not string \
matching. For example: a dashboard literally named "Rollouts Monitoring" with per-product-line "Pre-"/"Before"/ \
"After" charts is exactly the kind of before/after or test-vs-comparison structure a question about \
incremental impact needs, even though neither "control" nor "incremental" appears in any of those names; a \
"Completion Rate" or "CR" chart is exactly what a "conversion" question needs, even if the question doesn't \
say "completion"; a "Personalised" vs "Static" pair of charts on the same topic is itself a real comparison. \
Read the real dashboard names as organizing categories, not noise - they tell you what kind of analysis a \
chart belongs to. Err toward including a chart whose name plausibly represents the KIND of thing being \
asked about, even if the exact wording differs - a few extra real charts checked and found not to apply \
costs little; missing the one real chart that does answer the question costs the whole answer.

The question:
{question}

Real chart catalog (dashboard :: chart name):
{catalog}
"""


def _select_relevant_charts(question: str, catalog: list, llm) -> list:
    """Real reasoning over the REAL MoEngage schema - the direct analogy to
    a SQL agent inspecting real table/column schema before writing a
    query. Every decision here is a genuine LLM judgment grounded in the
    real catalog, then validated against it: a selected label that isn't
    actually in the real catalog (a hallucinated chart name) is dropped,
    never trusted into a fetch - the same validate-before-trust principle
    used throughout this codebase for other LLM-produced values. Returns
    the real chart refs to actually fetch - empty if nothing plausibly
    applies, or on any failure (fails closed to 'nothing selected', not to
    guessing)."""
    catalog_by_label = {f"{r['dashboard_name']} :: {r['chart_name']}": r for r in catalog}
    catalog_text = "\n".join(f"- {label}" for label in catalog_by_label)

    structured_llm = llm.with_structured_output(_ChartSelection)
    prompt = ChatPromptTemplate.from_messages([("human", _CATALOG_SELECTION_PROMPT)])
    chain = prompt | structured_llm
    try:
        result: _ChartSelection = _invoke_with_retry(
            chain, {"question": question, "chart_count": len(catalog_by_label), "catalog": catalog_text},
            label="MoEngage chart selection call",
        )
    except Exception as exc:  # noqa: BLE001 - fail closed to "nothing selected"
        logger.warning("MoEngage chart selection failed for %r: %s", question, exc)
        return []

    selected = []
    for raw_label in result.chart_labels:
        ref = catalog_by_label.get(raw_label.strip())
        if ref:
            selected.append(ref)
        else:
            logger.warning(
                "MoEngage chart selection returned a label not in the real catalog, dropped: %r", raw_label,
            )
    logger.info(
        "MoEngage chart selection for %r: %s (%s)",
        question, [f"{r['dashboard_name']} :: {r['chart_name']}" for r in selected], result.reasoning,
    )
    return selected


def _fetch_selected_charts(selected: list) -> list:
    """Fetches real data for ONLY the specific charts selection chose - a
    real efficiency win over fetching all ~138 charts regardless of
    relevance, and the direct MoEngage analogy to running a query against
    only the tables/columns actually needed. Serial, not parallelized like
    the full-catalog fetch - a genuine selection is normally a handful of
    charts, not 138, so the added complexity of a thread pool isn't worth
    it here."""
    results = []
    for ref in selected:
        label = f"{ref['dashboard_name']} :: {ref['chart_name']}"
        try:
            data = moengage_client.get_chart_data(ref["dashboard_id"], ref["chart_id"])
            results.append({"label": label, "data": data.get("data"), "error": None})
        except Exception as exc:  # noqa: BLE001 - reported per-chart, not raised
            logger.warning("MoEngage chart %r failed: %s", label, exc)
            results.append({"label": label, "data": None, "error": str(exc)})
    return results


class _ChartNarrative(BaseModel):
    answers_question: bool = Field(
        description="True if the real data below genuinely answers or informs this question EITHER fully "
        "or PARTIALLY - a partial, honestly-caveated answer counts as True, it is NOT the same as a full "
        "answer being required. Two specific traps to actively avoid, both real and caught live: (1) a "
        "question about 'incremental revenue' answered with a real order/completion/booking-count "
        "comparison across a before/after or rollout-group split, when exact revenue isn't tracked but a "
        "real, directly comparable count is - that IS a genuine, honest answer to the underlying business "
        "question, just correctly labeled for what it actually is, never thrown away just because its unit "
        "isn't the literal word used in the question; (2) real data covering MOST but not all of what was "
        "asked (e.g. 4 of 5 product lines named in the question, or the metric for one segment but not "
        "another) - report what IS genuinely known for the part(s) it covers, and say plainly which part(s) "
        "it doesn't, rather than refusing the whole thing because one part is incomplete. A real, useful, "
        "honestly-scoped partial answer is what a good analyst actually gives; refusing outright because "
        "the data isn't perfectly complete is not more honest, it's less useful for no real gain in "
        "accuracy. False only if NONE of the real data below relates to what was asked at all - a chart's "
        "NAME can be a plausible guess that turns out wrong once you see its real contents; that's a "
        "genuine miss worth reporting honestly, this field is not asking you to grade completeness."
    )
    summary: str = Field(
        description="If answers_question=True: three to six short plain-English sentences reporting ONLY "
        "what is genuinely present in the real data below - COMPUTE it (e.g. sum a funnel step's real "
        "values across every date entry, then a real rate) rather than eyeballing a couple of rows; never "
        "invent or estimate a number that isn't really there, and never relabel one real metric as a "
        "different one (report a real order/completion count honestly as a count, never claim it's revenue "
        "just because the question asked about revenue - state plainly what it actually measures). If part "
        "of the question isn't covered by the real data, say which part plainly in one added sentence - "
        "don't let that stop you from reporting the part(s) that ARE genuinely covered. If answers_question"
        "=False: one honest sentence saying plainly that this real data doesn't actually cover what was "
        "asked (and briefly what it covers instead, if that's useful context). Either way, write this as a "
        "finished answer a customer-facing analyst would say out loud: never say 'chart', 'dashboard', "
        "'data shows', or name a raw metric/field label - translate every one into the plain business term "
        "(e.g. a chart tracking step-1-to-step-2 dropoff on a winback flow becomes 'winback emails that get "
        "a response', not a description of the chart). This may be used directly as someone's final answer "
        "with no further editing."
    )


_CHART_NARRATIVE_PROMPT = """These real charts were selected as plausibly relevant to the question below - \
here is their REAL data. Look at it genuinely, validate whether it actually answers what was asked, and \
report honestly either way (see answers_question).

HOW TO READ THIS DATA: MoEngage chart payloads are opaque and vary by analysis type - work out each one's \
real shape from what's actually in it, don't assume a fixed schema. Two patterns worth actually computing, \
not just eyeballing, when you see them: (1) a list of records with a 'step'/'metric'/date-like field (a \
funnel) - sum 'metric' per step across every date entry for that chart to get real step totals, then a real \
completion rate (last step's total over first step's total); (2) a genuine BEFORE/AFTER, PRE/POST, or \
TEST/CONTROL pair of charts on the same real-world metric (their names will say so, e.g. two charts named \
"[Before ...] X" and "[After ...] X") - compute each one's own rate the same way, then compare the two real \
rates directly; that comparison, computed from the real numbers, IS the incremental-impact answer, not \
something to eyeball from a couple of rows. Never estimate a rate from a partial glance when the real data \
below lets you actually total it up.

The question:
{question}

Selected charts (label: real data):
{charts}
"""


def _narrate_from_charts(question: str, fetched: list, llm) -> _ChartNarrative:
    per_chart_cap = _per_chart_char_cap(len(fetched))
    lines = [
        f"- {c['label']}: [unavailable - {c['error']}]" if c["error"]
        else f"- {c['label']}: {str(c['data'])[:per_chart_cap]}"
        for c in fetched
    ]
    structured_llm = llm.with_structured_output(_ChartNarrative)
    prompt = ChatPromptTemplate.from_messages([("human", _CHART_NARRATIVE_PROMPT)])
    chain = prompt | structured_llm
    return _invoke_with_retry(
        chain, {"question": question, "charts": "\n".join(lines)}, label="MoEngage narrative call",
    )


def gather_moengage_context(question: str, llm) -> tuple:
    """Returns (text, relevant, raw_summary, checked). Real reasoning
    pipeline, no keyword shortcut anywhere: read the real chart catalog
    (the MoEngage "schema") -> reason about which real charts genuinely
    help -> fetch only those -> validate against their real data ->
    narrate. `relevant` is the only signal callers should use to decide
    whether real MoEngage data actually informed the answer - never infer
    it from `text`'s wording, which exists purely for display/context (it
    names the real chart count, deliberately, for a downstream agent's own
    transparency) and can describe a "nothing relevant" outcome in several
    different ways. `raw_summary` is the plain, customer-voiced finding
    alone (empty string if not relevant) - safe to use directly as a final
    answer with no further editing (unlike `text`, it never mentions
    charts/dashboards - see _ChartNarrative.summary's own docstring), for
    a caller that determined MoEngage is the ONLY source this question
    needs. `checked` - real gap this closes: True whenever a genuine
    catalog-selection-fetch-validate pass actually ran, even if it found
    nothing relevant, as distinct from never running at all (not
    configured, catalog listing failed, no charts exist) - a caller can
    still tell a downstream answer-writer "MoEngage was genuinely checked,
    here's the real reason it found nothing" instead of that writer
    defaulting to a vague, misleading "not available" that implies no one
    looked."""
    if not moengage_client.is_configured():
        return "MoEngage is not connected.", False, "", False

    try:
        catalog = moengage_client.list_chart_catalog()
    except Exception as exc:  # noqa: BLE001 - report, don't propagate
        logger.warning("Failed to list MoEngage chart catalog: %s", exc)
        return f"MoEngage is connected but the chart catalog couldn't be listed ({exc}).", False, "", False

    if not catalog:
        return "MoEngage is connected but has no dashboards/charts yet.", False, "", False

    selected = _select_relevant_charts(question, catalog, llm)
    if not selected:
        return (
            f"Checked the real MoEngage chart catalog ({len(catalog)} charts) - none are relevant to this "
            "question.", False, "", True,
        )

    fetched = _fetch_selected_charts(selected)
    failed = [c for c in fetched if c["error"]]

    try:
        narrative = _narrate_from_charts(question, fetched, llm)
    except Exception as exc:  # noqa: BLE001 - a narration failure shouldn't kill the caller
        logger.warning("MoEngage narrative call failed for %r: %s", question, exc)
        return (
            f"MoEngage is connected ({len(selected)} relevant chart(s) found) but the narrative step "
            "failed.", False, "", True,
        )

    if not narrative.answers_question:
        return (
            f"Checked {len(selected)} real MoEngage chart(s) that looked relevant, but they don't actually "
            f"answer this question: {narrative.summary}", False, "", True,
        )

    notes = f"MoEngage campaign/engagement data ({len(selected)} relevant chart(s) checked):\n{narrative.summary}"
    if failed:
        notes += f"\n({len(failed)} chart(s) could not be fetched and were excluded.)"
    return notes, True, narrative.summary, True

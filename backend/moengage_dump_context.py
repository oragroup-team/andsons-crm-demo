"""MoEngage retrieval for the Analytics Chat agent (agents/analytics_agent.py
ONLY) - the DUMP-based mechanism, not the chart-catalog reasoning
moengage_summary.py implements. moengage_summary.py is NOT touched or
deleted by this file - it's still fully intact and still actively used by
agents/insight_agent.py, which keeps importing it directly. This is a
deliberate, parallel replacement scoped to analytics_agent.py alone: that
agent's one import line was pointed at this module instead, and the old
mechanism is left in place, unused from there, rather than removed - see
analytics_agent.py's own comment at the import site.

MECHANISM (exactly as requested): every time this module is asked about a
question, it (1) runs a FRESH full-account flow dump - moengage_export/
dump_all_flow_stats.dump_all(), the same script built standalone for a
one-off spreadsheet handoff - to a per-call temp Excel/SQLite file pair,
(2) reads the Excel with pandas to find and summarize whatever's relevant
to the question, then (3) deletes both files it just wrote in a `finally`,
so nothing dump-related is ever left on disk between questions or across
concurrent requests (each call uses its own uuid-suffixed temp path -
matters because Slack questions each run on their own background thread,
see moengage_client.py's module docstring).

REAL COST TRADEOFF, stated plainly rather than hidden: a fresh full-account
dump (150+ real flows, ~500 real campaigns, confirmed live) takes on the
order of MINUTES, not seconds - confirmed live building this script for a
one-off handoff. This module only pays that cost when the cheap flow-name
selection step below judges the question plausibly needs flow/campaign
data at all, but even then, a chat question answered via this path is now
itself minutes-slow. That's a real, meaningful UX regression versus the
chart-catalog mechanism this replaces (which fetched only the handful of
already-built charts a question needed, not the whole account) - accepted
here because the dump-based mechanism is what was explicitly asked for;
worth revisiting (e.g. a short-TTL cache of the last dump) if this latency
turns out to be a problem in practice for a live chat agent.

Same real 4-tuple return contract as moengage_summary.gather_moengage_
context(question, llm) -> (text, relevant, raw_summary, checked) - see
that function's own docstring for what each element means. Matching it
exactly is why analytics_agent.py only had to change its import line; none
of its own downstream verification/source-selection/prompt-injection logic
needed to change."""
import logging
import os
import sys
import tempfile
import uuid
from typing import List

import pandas as pd
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

import moengage_client

# dump_all_flow_stats.py lives in moengage_export/, not directly in backend/
# (where this file and moengage_client.py live) - add it to sys.path the
# same way every moengage_export/*.py script already adds ITS OWN
# dependencies (see dump_all_flow_stats.py's own top-of-file sys.path
# lines), rather than turning moengage_export/ into a real installed
# package just for this one import.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "moengage_export"))
from dump_all_flow_stats import dump_all  # noqa: E402

logger = logging.getLogger("moengage_dump_context")

# Same defaults dump_all_flow_stats.py itself uses standalone (30-day
# window, every real flow status) - a live chat question gets the same
# real account-wide picture a human running the script by hand would.
_DUMP_DAYS = 30
_DUMP_STATUS = None


def _invoke_with_retry(chain, payload: dict, attempts: int = 5, label: str = "MoEngage dump LLM call"):
    """Same real Groq retry fix as moengage_summary.py's own local copy
    (forced tool-calling mode's 'Tool choice is required, but model did
    not call a tool' 400 on a transient failure) - duplicated here rather
    than imported, so this module has zero dependency on moengage_summary.
    py (deliberate: that module is the backed-up, no-longer-used-from-here
    mechanism, not a shared library for this one)."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return chain.invoke(payload)
        except Exception as exc:  # noqa: BLE001 - every attempt logged, caller decides final handling
            last_exc = exc
            logger.warning("%s failed (attempt %d/%d): %s", label, attempt + 1, attempts, exc)
    raise last_exc


class _FlowSelection(BaseModel):
    flow_names: List[str] = Field(
        description="Every real flow name (exact text, copied verbatim from the list below - never invent, "
        "abbreviate, or paraphrase one) that could plausibly help answer this question, based on genuinely "
        "understanding what each flow's own name says it's for. Empty list if, after actually reading the "
        "real list, nothing plausibly applies - do not force a connection just to return something."
    )
    reasoning: str = Field(description="One or two sentences: your real reasoning for these flows (or for none).")


_FLOW_SELECTION_PROMPT = """Below is the REAL, COMPLETE list of every flow name in this andSons MoEngage \
workspace ({flow_count} flows total) - just pulled fresh, this run, directly from MoEngage. Read the real \
question below, then genuinely reason about which of these real flows, if any, would actually help answer \
it, based on what each flow's own name tells you it's for (e.g. "Abandon Cart DC_WL", "Winback_Subscription_\
ED_3M", "WelcomeFlow_New") - not a literal keyword match, the same way an analyst reads a list of campaign \
names. Err toward including a flow whose name plausibly represents the KIND of thing being asked about.

The question:
{question}

Real flow names:
{flow_names}
"""


def _select_relevant_flows(question: str, flow_names: list, llm) -> list:
    """Real reasoning over the REAL, freshly-dumped flow list - the direct
    analogy to moengage_summary._select_relevant_charts, just over flow
    names instead of chart labels. A selected name that isn't actually in
    the real list (a hallucinated flow name) is dropped, never trusted -
    same validate-before-trust principle used throughout this codebase.
    Fails closed to 'nothing selected' on any error, never to guessing."""
    structured_llm = llm.with_structured_output(_FlowSelection)
    prompt = ChatPromptTemplate.from_messages([("human", _FLOW_SELECTION_PROMPT)])
    chain = prompt | structured_llm
    try:
        result: _FlowSelection = _invoke_with_retry(
            chain,
            {"question": question, "flow_count": len(flow_names), "flow_names": "\n".join(f"- {n}" for n in flow_names)},
            label="MoEngage dump flow selection call",
        )
    except Exception as exc:  # noqa: BLE001 - fail closed to "nothing selected"
        logger.warning("MoEngage dump flow selection failed for %r: %s", question, exc)
        return []

    real_names = set(flow_names)
    selected, dropped = [], []
    for raw_name in result.flow_names:
        name = raw_name.strip()
        (selected if name in real_names else dropped).append(name)
    if dropped:
        logger.warning("MoEngage dump flow selection returned name(s) not in the real dump, dropped: %s", dropped)
    logger.info("MoEngage dump flow selection for %r: %s (%s)", question, selected, result.reasoning)
    return selected


class _DumpNarrative(BaseModel):
    answers_question: bool = Field(
        description="True if the real data below genuinely answers or informs this question EITHER fully or "
        "PARTIALLY - an honestly-caveated partial answer counts as True. Real data covering most but not all "
        "of what was asked (e.g. some but not all flows named in the question) still counts True - report "
        "what IS genuinely known and say plainly what isn't, rather than refusing outright. False only if "
        "NONE of the real data below relates to what was asked at all."
    )
    summary: str = Field(
        description="If answers_question=True: three to six short plain-English sentences reporting ONLY "
        "what is genuinely present in the real data below - compute a real total/rate from the real numbers "
        "shown (e.g. sum Sent/Delivered/Revenue across the listed nodes) rather than eyeballing one row; "
        "never invent or estimate a number that isn't really there. If part of the question isn't covered, "
        "say which part plainly in one added sentence. If answers_question=False: one honest sentence saying "
        "plainly that this real data doesn't cover what was asked. Either way, write this as a finished "
        "answer a customer-facing analyst would say out loud: never say 'flow dump', 'spreadsheet', 'row', "
        "or a raw field name like 'adjusted_opened' - translate every one into the plain business term (e.g. "
        "a flow's WhatsApp action node's real numbers become 'the abandoned-cart WhatsApp message'). This "
        "may be used directly as someone's final answer with no further editing."
    )


_DUMP_NARRATIVE_PROMPT = """These real flows were selected as plausibly relevant to the question below - here \
is their REAL data, just pulled fresh from MoEngage this run (one entry per flow, with that flow's own total \
across all its send nodes, then each individual send node's own real Attempted/Sent/Delivered/Opened/\
Adjusted-Opened/Clicked/Conversions/Revenue numbers for the {days}-day window ending today). Look at it \
genuinely, validate whether it actually answers what was asked, and report honestly either way.

The question:
{question}

Selected flows (real data):
{flow_data}
"""


def _narrate_from_rows(question: str, flow_data_text: str, llm) -> _DumpNarrative:
    structured_llm = llm.with_structured_output(_DumpNarrative)
    prompt = ChatPromptTemplate.from_messages([("human", _DUMP_NARRATIVE_PROMPT)])
    chain = prompt | structured_llm
    return _invoke_with_retry(
        chain, {"question": question, "flow_data": flow_data_text, "days": _DUMP_DAYS},
        label="MoEngage dump narrative call",
    )


_NODE_METRIC_COLUMNS = ["attempted", "sent", "delivered", "opened", "adjusted_opened", "clicked", "conversions", "revenue"]


def _flow_text(df: pd.DataFrame, flow_name: str) -> str:
    """One real flow's data, formatted as compact text: the flow-level
    total across every send node, then each individual send node's own
    real numbers - so both a flow-level question ("how did X do overall")
    and a step-level one ("what's the open rate on the second email") can
    be answered from the same block. Non-send nodes (waits/conditions/
    branches) are left out here - they carry no metric columns in the
    dump (see dump_all_flow_stats.py's own docstring for why), so they'd
    add nothing but noise to this text."""
    sub = df[df["flow_name"] == flow_name]
    if sub.empty:
        return f"FLOW: {flow_name} - no data in this run's dump (may have been deleted/renamed since)."

    status = sub["flow_status"].iloc[0] if "flow_status" in sub else "?"
    send_rows = sub[sub["campaign_id"].notna()]
    lines = [f"FLOW: {flow_name} (status={status}, {len(send_rows)} send node(s), {len(sub)} total node(s))"]

    if not send_rows.empty:
        totals = send_rows[_NODE_METRIC_COLUMNS].sum(numeric_only=True)
        lines.append("  FLOW TOTAL: " + ", ".join(f"{col}={totals[col]:g}" for col in _NODE_METRIC_COLUMNS if totals[col]))
        for _, row in send_rows.iterrows():
            metrics = ", ".join(
                f"{col}={row[col]:g}" for col in _NODE_METRIC_COLUMNS if pd.notna(row[col])
            )
            channel = row.get("channel") or row.get("node_type")
            lines.append(f"  - {row['node_label']} ({channel}): {metrics}")
    else:
        lines.append("  (no send nodes with real Campaign Stats data in this window)")
    return "\n".join(lines)


def gather_moengage_context(question: str, llm) -> tuple:
    """Returns (text, relevant, raw_summary, checked) - same real contract
    as moengage_summary.gather_moengage_context (see that function's own
    docstring for the precise meaning of each element); this is the
    dump-based mechanism described in this module's own docstring above.
    `text` deliberately includes the real underlying numbers (not just the
    LLM's own narrative prose) so downstream number-verification in
    analytics_agent.py has real ground truth to check against, not just
    the narrative checking itself."""
    if not moengage_client.campaigns_api_configured():
        return "MoEngage is not connected.", False, "", False

    run_id = uuid.uuid4().hex
    xlsx_path = os.path.join(tempfile.gettempdir(), f"moengage_dump_context_{run_id}.xlsx")
    db_path = os.path.join(tempfile.gettempdir(), f"moengage_dump_context_{run_id}.db")
    try:
        try:
            logger.info("Running a fresh full-account MoEngage flow dump for: %r", question)
            dump_all(_DUMP_DAYS, _DUMP_STATUS, xlsx_path, db_path)
        except Exception as exc:  # noqa: BLE001 - report, don't propagate
            logger.warning("MoEngage full-account dump failed: %s", exc)
            return f"MoEngage is connected but the flow dump failed ({exc}).", False, "", False

        try:
            df = pd.read_excel(xlsx_path)
        except Exception as exc:  # noqa: BLE001 - report, don't propagate
            logger.warning("Failed to read the MoEngage dump Excel file: %s", exc)
            return f"MoEngage dump ran but the Excel file couldn't be read ({exc}).", False, "", False

        if df.empty or "flow_name" not in df.columns:
            return "MoEngage is connected but the fresh dump returned no flows.", False, "", False

        flow_names = sorted(df["flow_name"].dropna().unique().tolist())
        selected = _select_relevant_flows(question, flow_names, llm)
        if not selected:
            return (
                f"Checked a real, fresh MoEngage flow dump ({len(flow_names)} flows) - none are relevant to "
                "this question.", False, "", True,
            )

        flow_data_text = "\n\n".join(_flow_text(df, name) for name in selected)

        try:
            narrative = _narrate_from_rows(question, flow_data_text, llm)
        except Exception as exc:  # noqa: BLE001 - a narration failure shouldn't kill the caller
            logger.warning("MoEngage dump narrative call failed for %r: %s", question, exc)
            return (
                f"MoEngage is connected ({len(selected)} relevant flow(s) found in a fresh dump) but the "
                "narrative step failed.", False, "", True,
            )

        if not narrative.answers_question:
            return (
                f"Checked {len(selected)} real MoEngage flow(s) from a fresh dump that looked relevant, but "
                f"they don't actually answer this question: {narrative.summary}", False, "", True,
            )

        notes = (
            f"MoEngage campaign/engagement data (fresh full-account dump, {len(selected)} relevant flow(s) "
            f"checked):\n{narrative.summary}\n\nUnderlying real numbers this was drawn from:\n{flow_data_text}"
        )
        return notes, True, narrative.summary, True
    finally:
        # Real requirement, not optional cleanup: nothing dump-related
        # should be left on disk once this call is done, success or not.
        for path in (xlsx_path, db_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as exc:  # noqa: BLE001 - log, don't fail the whole call over a cleanup miss
                logger.warning("Could not delete temp MoEngage dump file %r: %s", path, exc)

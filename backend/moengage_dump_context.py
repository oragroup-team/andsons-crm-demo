"""MoEngage retrieval for the Analytics Chat agent (agents/analytics_agent.py
ONLY) - the DUMP-based mechanism, not the chart-catalog reasoning
moengage_summary.py implements. moengage_summary.py is NOT touched or
deleted by this file - it's still fully intact and still actively used by
agents/insight_agent.py, which keeps importing it directly.

MECHANISM (real architecture change, 2026-09-24): this module used to run a
FRESH full-account dump on every single question (minutes-slow, see git
history for that version's own docstring on the real cost). It now READS
the pre-generated files moengage_export/daily_flow_tracker.py's own daily
cron run already produced - moengage_export/app.py's /cron/daily-flow-
tracker endpoint runs that script once a day at 06:00 SGT (see app.py's own
docstring on that route) - rather than paying the live-dump cost per
question. Real trade made explicitly: this module can now be seconds-fast
per question instead of minutes-slow, at the real cost that its numbers
are only as fresh as the most recent daily run, not live-to-the-second.
That's the same trade the actual daily_flow_tracker.xlsx handoff to Bryan
already makes - this module just reads the same real files rather than
re-deriving them.

TWO REAL FILES READ, not one - see daily_flow_tracker.py's own docstring
for how each is produced:
  1. THE LATEST PULL (daily_flow_tracker.csv) - most recent day's real
     numbers, one row per flow's send node - used for "how is X doing"
     / current-state questions.
  2. THE HISTORY (daily_flow_tracker_history.csv) - every day's numbers,
     upserted not overwritten - used to give a genuine multi-day/periodic
     trend for a selected flow (e.g. day-by-day Sent/Revenue over the last
     couple of weeks), not just a single day's snapshot. This is the real
     fix for a live, previously-true complaint: earlier versions of this
     whole mechanism only ever had "today's" or "yesterday's" one-day
     window, with no way to answer a genuinely comparative/trend question
     ("is this getting better or worse") at all.

GCS-AWARE READING - Cloud Run's own local disk is NOT durable across
instances/redeploys (confirmed via Cloud Run's documented execution model),
and this same deployed service can run more than one instance, so the
instance answering a Slack question is not guaranteed to be the same one
the daily cron endpoint ran on. When MOENGAGE_EXPORT_GCS_BUCKET is set,
both files are downloaded fresh from GCS before being read - the real,
durable source of truth in production. When unset (local/dev use), both
are just read directly off local disk - see _load_dataframe()."""
import logging
import os
import re
import sys
from typing import List, Optional

import pandas as pd
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

import moengage_client

# daily_flow_tracker.py lives in moengage_export/, not directly in backend/
# (where this file and moengage_client.py live) - add it to sys.path the
# same way every moengage_export/*.py script already adds ITS OWN
# dependencies, rather than turning moengage_export/ into a real installed
# package just for this one import.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "moengage_export"))
from daily_flow_tracker import _DEFAULT_OUT_PREFIX, _DEFAULT_HISTORY_PATH, _BRAND_SHEET_NAMES  # noqa: E402

logger = logging.getLogger("moengage_dump_context")

_LATEST_CSV_PATH = f"{_DEFAULT_OUT_PREFIX}.csv"
_HISTORY_CSV_PATH = _DEFAULT_HISTORY_PATH
_GCS_BUCKET = os.environ.get("MOENGAGE_EXPORT_GCS_BUCKET")
_TREND_DAYS = 14  # how many of the most recent real days' history to show per selected flow


def _load_dataframe(local_path: str, gcs_blob_name: str) -> pd.DataFrame:
    """Reads one real CSV, GCS-aware - see module docstring for why this
    isn't just a plain local file read in production. Raises FileNotFound
    error / lets the real pandas/GCS exception propagate to the caller,
    which already handles "file doesn't exist yet" (e.g. before the first
    real cron run) as its own real, honest outcome rather than pretending
    empty data is normal."""
    if _GCS_BUCKET:
        from google.cloud import storage

        client = storage.Client()
        blob = client.bucket(_GCS_BUCKET).blob(f"moengage_export/{gcs_blob_name}")
        local_cache = os.path.join("/tmp", gcs_blob_name)
        blob.download_to_filename(local_cache)
        return pd.read_csv(local_cache)
    return pd.read_csv(local_path)


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
    flow_labels: List[str] = Field(
        description="Every real 'Brand: flow name' label (exact text, copied verbatim from the list below - "
        "never invent, abbreviate, or paraphrase one, and never drop the 'Brand: ' prefix) that could "
        "plausibly help answer this question, based on genuinely understanding what each flow's own name "
        "says it's for. Empty list if, after actually reading the real list, nothing plausibly applies - do "
        "not force a connection just to return something."
    )
    reasoning: str = Field(description="One or two sentences: your real reasoning for these flows (or for none).")


_FLOW_SELECTION_PROMPT = """Below is the REAL, COMPLETE list of every flow in every real MoEngage brand/market \
workspace this system has access to ({flow_count} flows total across all brands) - from the most recent daily \
pull. Each one is labeled "Brand: flow name" - the SAME flow name can genuinely exist under multiple different \
real brands (e.g. "Payment_Failed" exists separately under andSons SG AND Ova SG - two real, different flows, \
never the same one), so always select the FULL "Brand: flow name" label, never just the flow name part. Read \
the real question below, then genuinely reason about which of these real flows, if any, would actually help \
answer it, based on what each flow's own name tells you it's for (e.g. "Abandon Cart DC_WL", \
"Winback_Subscription_ED_3M", "WelcomeFlow_New") - not a literal keyword match, the same way an analyst reads \
a list of campaign names. If the question names or implies a specific brand/market (e.g. "Ova SG", "andSons \
Malaysia"), select ONLY that brand's matching flow(s) - never blend a different brand's flow in just because \
it has a similar or identical name. If the question doesn't name a brand, select the plausibly relevant \
flow(s) from every brand they exist under.

The question:
{question}

Real flow labels ("Brand: flow name"):
{flow_names}
"""


def _select_relevant_flows(question: str, flow_labels: list, llm) -> list:
    """Real reasoning over the REAL, most-recently-pulled, brand-labeled
    flow list - the direct analogy to moengage_summary._select_relevant_
    charts, just over "Brand: flow name" labels instead of chart labels.
    Brand-labeled (not just flow_name) because the same flow name can
    genuinely exist under more than one real brand's own workspace
    (confirmed live, 2026-09-24: 34 real flow names collide across at
    least 2 brands, one across 4) - selecting by bare flow_name alone
    would silently blend two different brands' real numbers together as
    if they were one flow, exactly the kind of real-number-wrong-scope
    mistake analytics_agent.py's own system prompt calls out as a serious
    failure. A selected label that isn't actually in the real list (a
    hallucinated one) is dropped, never trusted - same validate-before-
    trust principle used throughout this codebase. Fails closed to
    'nothing selected' on any error, never to guessing."""
    structured_llm = llm.with_structured_output(_FlowSelection)
    prompt = ChatPromptTemplate.from_messages([("human", _FLOW_SELECTION_PROMPT)])
    chain = prompt | structured_llm
    try:
        result: _FlowSelection = _invoke_with_retry(
            chain,
            {"question": question, "flow_count": len(flow_labels), "flow_names": "\n".join(f"- {n}" for n in flow_labels)},
            label="MoEngage dump flow selection call",
        )
    except Exception as exc:  # noqa: BLE001 - fail closed to "nothing selected"
        logger.warning("MoEngage dump flow selection failed for %r: %s", question, exc)
        return []

    real_labels = set(flow_labels)
    selected, dropped = [], []
    for raw_label in result.flow_labels:
        label = raw_label.strip()
        (selected if label in real_labels else dropped).append(label)
    if dropped:
        logger.warning("MoEngage dump flow selection returned label(s) not in the real dump, dropped: %s", dropped)
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
        "shown (e.g. sum Sent/Delivered/Revenue across the listed nodes, or describe a real day-by-day trend "
        "from the history section if the question is about change over time) rather than eyeballing one row; "
        "never invent or estimate a number that isn't really there. If part of the question isn't covered, "
        "say which part plainly in one added sentence. If answers_question=False: one honest sentence saying "
        "plainly that this real data doesn't cover what was asked. Either way, write this as a finished "
        "answer a customer-facing analyst would say out loud: never say 'flow dump', 'spreadsheet', 'row', "
        "or a raw field name like 'adjusted_opened' - translate every one into the plain business term (e.g. "
        "a flow's WhatsApp action node's real numbers become 'the abandoned-cart WhatsApp message'). This "
        "may be used directly as someone's final answer with no further editing."
    )


_DUMP_NARRATIVE_PROMPT = """These real flows were selected as plausibly relevant to the question below. For \
each one you get (1) its most recent day's real numbers per send node, and (2) a real day-by-day trend from \
up to the last {trend_days} real days on record (empty/short if the daily pull hasn't been running that long \
yet - say so plainly if a real trend question can't be answered because of that, don't fake a trend from one \
data point). Look at it genuinely, validate whether it actually answers what was asked, and report honestly \
either way.

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
        chain, {"question": question, "flow_data": flow_data_text, "trend_days": _TREND_DAYS},
        label="MoEngage dump narrative call",
    )


_TEST_DUPLICATE_FLOW_NAME_RE = re.compile(r"testing|duplicate|test[_ ]|^test$", re.IGNORECASE)
_QUESTION_WANTS_TEST_FLOWS_RE = re.compile(r"\b(test|testing|duplicate|internal)\b", re.IGNORECASE)


def _is_test_or_duplicate_flow_name(flow_name: str) -> bool:
    return bool(_TEST_DUPLICATE_FLOW_NAME_RE.search(flow_name))


_NODE_METRIC_COLUMNS = [
    "attempted", "sent", "delivered", "opened", "adjusted_opened", "clicked",
    "failed", "bounced", "unsubscribed", "complaints",
    "conversions", "revenue",
]
# Real, live-caught gap this fixes (Sept 25 2026 OVA SG incident, Bryan Chang):
# a question asking "how many attempted/opened" for a PAST date (not the single
# most-recent pull day) could never be answered from this trend section at all -
# attempted/opened were missing here even though the real daily_flow_tracker.csv
# genuinely has them per day. That forced a fallback to querying BigQuery
# directly, where the agent picked the wrong table entirely (moengage_campaigns_
# email - a static Aug-20 snapshot with no Brand column at all, see analytics_
# agent.py's own schema notes) and returned a wrong number with high confidence.
# Every metric a real "how did this flow do on day X" question could plausibly
# ask for is now in this list - correctness over brevity, since the previous
# "kept smaller" trade is exactly what caused the wrong answer.
_TREND_METRIC_COLUMNS = ["attempted", "sent", "delivered", "opened", "failed", "bounced", "conversions", "revenue"]
_FAILURE_REASONS_COLUMN = "failure_reasons"  # concatenated text, never summed as a number - see daily_flow_tracker.py's own _TEXT_FIELDS


def _latest_flow_text(latest_df: pd.DataFrame, brand: str, flow_name: str) -> str:
    """One real flow's MOST RECENT day, formatted as compact text: an
    ALL-CHANNELS-COMBINED total, a PER-CHANNEL breakdown, then each
    individual send node's own real numbers - so a flow-level question
    ("how did X do"), a CHANNEL-specific one ("how many EMAILS"), and a
    step-level one ("what's the open rate on the second email") can all be
    answered from the same block. Non-send nodes (waits/conditions/
    branches) are left out - they carry no metric columns (see daily_flow_
    tracker.py's own docstring for why), so they'd add nothing but noise
    here. Filtered by BOTH brand and flow_name - see _select_relevant_
    flows' own docstring for why flow_name alone isn't enough (real name
    collisions across brands, confirmed live).

    REAL, LIVE-CAUGHT BUG THIS SECTION FIXES: a real flow (e.g. OVA SG's
    "Abandon Cart - WL") can have BOTH an Email send node AND a separate
    WhatsApp send node as parallel steps of the SAME flow (confirmed live:
    123 of 429 real flows across every brand mix 2+ channels this way).
    The combined total alone previously answered "how many emails were
    sent" with the EMAIL node's count blended together with the WhatsApp
    node's count (e.g. a real incident: 3 real emails sent got reported as
    6, because 3 WhatsApp sends on the same day were silently added in) -
    a real number, but for the wrong, broader population than what was
    asked. The per-channel breakdown below is what actually answers a
    channel-scoped question correctly; the combined total is now labelled
    unambiguously so it's never mistaken for one channel's own number.

    SECOND REAL FIX, same principle applied to DATE instead of channel:
    `latest_df` is no longer guaranteed to hold only a single calendar
    day's rows - the daily cron now also re-pulls a rolling window of the
    last few days to fix a separate real staleness bug (see daily_flow_
    tracker.build_tracker_with_refresh's own docstring), so this function
    must explicitly narrow to the MOST RECENT date_range_start present,
    never trust that "everything in latest_df" is automatically one day -
    that would silently blend multiple real days together exactly like
    the channel bug blended channels together."""
    sub = latest_df[(latest_df["flow_name"] == flow_name) & (latest_df["brand"] == brand)]
    brand_label = _BRAND_SHEET_NAMES.get(brand, brand)
    if sub.empty:
        return f"FLOW: {brand_label}: {flow_name} - no data in the most recent daily pull (may have been deleted/renamed since)."

    most_recent_date = sub["date_range_start"].max()
    sub = sub[sub["date_range_start"] == most_recent_date]

    status = sub["flow_status"].iloc[0] if "flow_status" in sub else "?"
    send_rows = sub[sub["campaign_id"].notna()]
    lines = [f"FLOW: {brand_label}: {flow_name} (status={status}, {len(send_rows)} send node(s), {len(sub)} total node(s))"]

    if not send_rows.empty:
        totals = send_rows[_NODE_METRIC_COLUMNS].sum(numeric_only=True)
        lines.append(
            f"  MOST RECENT DAY ON RECORD IS {most_recent_date} - TOTAL FOR THAT DATE, ALL CHANNELS COMBINED "
            "(email + WhatsApp + push together - use ONLY for a whole-flow question, NEVER as one channel's own "
            f"number, and NEVER as a stand-in for a DIFFERENT date someone actually asked about - if the question "
            f"named a date other than {most_recent_date}, look in the TREND section below instead): "
            + ", ".join(f"{col}={totals[col]:g}" for col in _NODE_METRIC_COLUMNS if totals[col])
        )
        channels_present = send_rows["channel"].dropna().str.upper().unique()
        if len(channels_present) > 1:
            for channel_value in sorted(channels_present):
                chan_rows = send_rows[send_rows["channel"].str.upper() == channel_value]
                chan_totals = chan_rows[_NODE_METRIC_COLUMNS].sum(numeric_only=True)
                lines.append(
                    f"  {most_recent_date}, {channel_value} ONLY (use THIS for a question about {channel_value.lower()} "
                    "specifically): " + ", ".join(f"{col}={chan_totals[col]:g}" for col in _NODE_METRIC_COLUMNS if chan_totals[col])
                )
        # Real named failure reasons (e.g. mo_engage_suppression, f_c_removed) -
        # a text field, never summed numerically like the metrics above. Real,
        # live-caught gap this fixes: this data genuinely exists per day in
        # daily_flow_tracker.csv but was never surfaced here at all, so a
        # "why did sends fail" question could never be answered from this
        # mechanism even for the single most recent day.
        if _FAILURE_REASONS_COLUMN in send_rows.columns:
            all_reasons = [r for r in send_rows[_FAILURE_REASONS_COLUMN].dropna().tolist() if r]
            if all_reasons:
                lines.append("  FAILURE REASONS (most recent day, across all send nodes): " + "; ".join(all_reasons))
        for _, row in send_rows.iterrows():
            metrics = ", ".join(f"{col}={row[col]:g}" for col in _NODE_METRIC_COLUMNS if pd.notna(row[col]))
            channel = row.get("channel") or row.get("node_type")
            line = f"  - {row['node_label']} ({channel}): {metrics}"
            reasons = row.get(_FAILURE_REASONS_COLUMN)
            if pd.notna(reasons) and reasons:
                line += f" [failure reasons: {reasons}]"
            lines.append(line)
    else:
        lines.append("  (no send nodes with real Campaign Stats data in this window)")
    return "\n".join(lines)


def _trend_text(history_df: Optional[pd.DataFrame], brand: str, flow_name: str) -> str:
    """Real day-by-day trend for one flow, from the real accumulated
    history file - this is the actual "periodic context and historic
    data" fix: earlier versions of this whole mechanism only ever had one
    day's numbers, with no way to tell "is this getting better or worse"
    at all. Sums each real distinct date_range_start's send-node rows for
    this flow, most recent first, capped at _TREND_DAYS real days - never
    fabricates a day that isn't actually on record. Filtered by BOTH
    brand and flow_name - see _select_relevant_flows' own docstring for
    why flow_name alone isn't enough (real name collisions across
    brands, confirmed live).

    REAL, LIVE-CAUGHT BUG THIS FIXES: this used to sum every send node for
    the day regardless of channel, so a flow with both an Email node and a
    WhatsApp node (123 of 429 real flows do - see _latest_flow_text's own
    docstring for the full incident) blended both channels' numbers into
    one per-day figure - there was NO way at all to recover a channel-
    specific historical number, unlike _latest_flow_text which at least
    has a per-node breakdown for the single latest day. Now groups by
    (day, channel) so a channel-scoped historical question (e.g. "how many
    EMAILS were sent on the 25th") can be answered correctly instead of
    silently getting an all-channels-combined number."""
    if history_df is None or history_df.empty:
        return "  TREND: no history recorded yet (daily pulls haven't run long enough to show a trend)."
    sub = history_df[
        (history_df["flow_name"] == flow_name) & (history_df["brand"] == brand) & history_df["campaign_id"].notna()
    ]
    if sub.empty:
        return "  TREND: no historical send-node data for this flow yet."

    channels_present = sub["channel"].dropna().str.upper().unique()
    recent_dates = sorted(sub["date_range_start"].unique())[-_TREND_DAYS:]
    sub = sub[sub["date_range_start"].isin(recent_dates)]

    if len(channels_present) <= 1:
        # Single channel - the old flat per-day total already IS the
        # channel-specific number, no ambiguity to fix, keep it simple.
        by_day = sub.groupby("date_range_start")[_TREND_METRIC_COLUMNS].sum(numeric_only=True).sort_index()
        lines = [f"  TREND (most recent {len(by_day)} real day(s) on record, oldest to newest):"]
        for date, row in by_day.iterrows():
            lines.append(f"    {date}: " + ", ".join(f"{col}={row[col]:g}" for col in _TREND_METRIC_COLUMNS if row[col]))
        return "\n".join(lines)

    lines = [f"  TREND (most recent {len(recent_dates)} real day(s) on record, oldest to newest, broken down by "
             "channel - this flow has more than one send channel, so a combined cross-channel number would blend "
             "them incorrectly):"]
    sub = sub.copy()
    sub["_channel_upper"] = sub["channel"].str.upper()
    by_day_channel = sub.groupby(["date_range_start", "_channel_upper"])[_TREND_METRIC_COLUMNS].sum(numeric_only=True)
    for date in sorted(recent_dates):
        day_lines = []
        for channel_value in sorted(channels_present):
            if (date, channel_value) not in by_day_channel.index:
                continue
            row = by_day_channel.loc[(date, channel_value)]
            metrics = ", ".join(f"{col}={row[col]:g}" for col in _TREND_METRIC_COLUMNS if row[col])
            if metrics:
                day_lines.append(f"{channel_value}: {metrics}")
        if day_lines:
            lines.append(f"    {date}: " + " | ".join(day_lines))
    return "\n".join(lines)


def _flow_text(latest_df: pd.DataFrame, history_df: Optional[pd.DataFrame], brand: str, flow_name: str) -> str:
    return _latest_flow_text(latest_df, brand, flow_name) + "\n" + _trend_text(history_df, brand, flow_name)


def gather_moengage_context(question: str, llm) -> tuple:
    """Returns (text, relevant, raw_summary, checked) - same real contract
    as moengage_summary.gather_moengage_context (see that function's own
    docstring for the precise meaning of each element); this is the
    read-the-daily-pull mechanism described in this module's own docstring
    above. `text` deliberately includes the real underlying numbers (not
    just the LLM's own narrative prose) so downstream number-verification
    in analytics_agent.py has real ground truth to check against, not just
    the narrative checking itself."""
    if not moengage_client.campaigns_api_configured():
        return "MoEngage is not connected.", False, "", False

    try:
        latest_df = _load_dataframe(_LATEST_CSV_PATH, "daily_flow_tracker.csv")
    except FileNotFoundError:
        return (
            "MoEngage is connected but no daily flow pull has run yet - the /cron/daily-flow-tracker job "
            "hasn't produced a file. Say so plainly rather than guessing at numbers.", False, "", False,
        )
    except Exception as exc:  # noqa: BLE001 - report, don't propagate
        logger.warning("Failed to read the latest MoEngage daily pull: %s", exc)
        return f"MoEngage is connected but the latest daily pull couldn't be read ({exc}).", False, "", False

    if latest_df.empty or "flow_name" not in latest_df.columns:
        return "MoEngage is connected but the latest daily pull has no flows in it.", False, "", False
    if "brand" not in latest_df.columns:
        # Real migration case, same reasoning as write_history()'s own -
        # a file pulled before multi-brand support existed only ever had
        # the one original workspace (andSons SG).
        latest_df = latest_df.copy()
        latest_df["brand"] = "AS_SG"

    try:
        history_df = _load_dataframe(_HISTORY_CSV_PATH, "daily_flow_tracker_history.csv")
        if history_df is not None and not history_df.empty and "brand" not in history_df.columns:
            history_df = history_df.copy()
            history_df["brand"] = "AS_SG"
    except Exception as exc:  # noqa: BLE001 - a missing/unreadable history file degrades to "no trend", not a hard failure
        logger.warning("Could not read MoEngage history file (trend context will be unavailable): %s", exc)
        history_df = None

    # "Brand: flow name" labels, not bare flow names - real flow names
    # collide across brands (confirmed live, 2026-09-24: 34 do, one
    # across 4 brands at once), so the LLM must select by the full
    # disambiguated label - see _select_relevant_flows' own docstring.
    brand_flow_pairs = sorted(latest_df[["brand", "flow_name"]].dropna().drop_duplicates().itertuples(index=False, name=None))
    label_to_pair = {f"{_BRAND_SHEET_NAMES.get(b, b)}: {name}": (b, name) for b, name in brand_flow_pairs}
    flow_labels = sorted(label_to_pair.keys())

    # Real, live-confirmed contamination risk this guards against: 23 of
    # 393 real flow names across the account are test/duplicate artifacts
    # (e.g. "Internal Testing - Replenishment_ED_RX_OF" sitting right next
    # to the real production "Replenishment_ED_RX_OF") - near-identical
    # names an LLM flow-selection call could easily conflate or double-
    # select, silently blending test traffic into a real business answer.
    # Excluded from selection UNLESS the question itself is genuinely
    # asking about test/internal/duplicate flows - never hidden from a
    # question that actually wants them.
    if not _QUESTION_WANTS_TEST_FLOWS_RE.search(question):
        flow_labels = [label for label in flow_labels if not _is_test_or_duplicate_flow_name(label_to_pair[label][1])]

    selected_labels = _select_relevant_flows(question, flow_labels, llm)
    if not selected_labels:
        return (
            f"Checked the latest real MoEngage daily pull ({len(flow_labels)} flows across every connected "
            "brand) - none are relevant to this question.", False, "", True,
        )
    selected = [label_to_pair[label] for label in selected_labels]

    flow_data_text = "\n\n".join(_flow_text(latest_df, history_df, brand, name) for brand, name in selected)

    try:
        narrative = _narrate_from_rows(question, flow_data_text, llm)
    except Exception as exc:  # noqa: BLE001 - a narration failure shouldn't kill the caller
        logger.warning("MoEngage dump narrative call failed for %r: %s", question, exc)
        return (
            f"MoEngage is connected ({len(selected)} relevant flow(s) found) but the narrative step failed.",
            False, "", True,
        )

    if not narrative.answers_question:
        return (
            f"Checked {len(selected)} real MoEngage flow(s) that looked relevant, but they don't actually "
            f"answer this question: {narrative.summary}", False, "", True,
        )

    fetched_at = latest_df["fetched_at"].iloc[0] if "fetched_at" in latest_df.columns and not latest_df.empty else "unknown"
    notes = (
        f"MoEngage campaign/engagement data (daily pull, last refreshed {fetched_at}, {len(selected)} "
        f"relevant flow(s) checked):\n{narrative.summary}\n\nUnderlying real numbers this was drawn from:\n{flow_data_text}"
    )
    return notes, True, narrative.summary, True

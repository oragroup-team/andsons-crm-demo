"""Replaces Bryan's manual morning routine (per the 2026-09-22 Hari x Bryan
meeting + screen-share): opening each CRM flow in the MoEngage UI by hand,
setting the date range to "Yesterday", and reading real numbers off the
canvas to type into a tracking sheet - ~1-2 hours every morning. This pulls
the real numbers automatically, in a couple of minutes.

FIELD COVERAGE - every field pulled here was confirmed to actually exist by
dumping real raw Campaign Stats responses across EMAIL/WhatsApp/PUSH and
reading the real union of their keys, not assumed from one channel's shape
(see _extract_variation_stats()'s own docstring for exactly what's
included and what's deliberately excluded as redundant - e.g. PUSH uses
`impression` where EMAIL uses `open` and WhatsApp uses `read`, a real
difference an earlier version of this script missed entirely, silently
leaving every PUSH row's "opened" blank).

CONTROL GROUP - a real campaign with a control group enabled (confirmed
live: 6 in this account) gets a SECOND row per node here, same flow_id/
node_label/campaign_id, `variation` = "control_group" instead of
"all_variations" - MoEngage's own real, deliberately-held-back baseline
slice of the audience that got nothing, kept for measuring incremental
impact (compare its conversions/revenue against the "all_variations" row
for the same campaign_id). Every other real campaign (no control group
configured) gets exactly the one row it always has, `variation` =
"all_variations" - see _extract_by_variation()'s own docstring.

SCOPE: every real flow in the account, every category, every status -
whatever MoEngage actually has live at the moment this runs. That count
moves as flows get created/retired, so it's never hardcoded or assumed
here - always a fresh mc.list_all_flows() call, read fresh each run. No
name filter, no status filter, by design - two earlier versions of this
script narrowed scope first to "abandon"/"replenish" only, then to 4
keyword-matched categories; both were wrong to guess at scope instead of
covering everything real that exists.

TARGETED - deliberately left BLANK, not computed. Real correction from
Bryan directly: "Targeted" is a value stakeholders set themselves, not a
number MoEngage tracks - a prior version of this script tried to
approximate it via the Funnels Query API (treating it as the flow canvas's
own "Entered" figure), which was the wrong problem to solve AND unreliable
in practice (MoEngage's Funnels API failed outright on ~85% of real
queries tried, confirmed live, not a bug in this script). Dropping that
mechanism entirely removes both the wrong assumption and the API
dependency that made this script slow and flaky - what's left (Campaign
Stats only) is fast and 100% reliable. Fill in `targeted` yourselves, or
tell me the real rule and I'll wire it in properly.

SENT % - unlike TARGETED, this one IS populated, directly from MoEngage's
own real `sent_rate` field (confirmed live present on EMAIL campaigns,
e.g. 69.23%, 85.71% - genuinely ABSENT on WhatsApp/Push campaigns'
performance_stats, left blank there rather than guessed at). No external
"Targeted" figure needed for this - MoEngage already computes it.

BRAND/MARKET SCOPE - confirmed off Bryan's screen-share: the real ASSG/
ASMY/OVASG/OVAMY split lives in four separate primary SQL-warehouse
databases (ova_sg/as_sg/ova_my/as_my) and a separate per-brand MoEngage
workspace, neither of which this script touches (left aside per direct
instruction - MoEngage-only for this pass). This workspace's real
MOENGAGE_WORKSPACE_ID is recorded in every output row so it's always clear
which one real workspace these numbers come from - never assume it equals
any one brand/market cell in the tracking sheet without checking first.

USAGE
  python3 daily_flow_tracker.py                       # yesterday's numbers, Excel + CSV + history
  python3 daily_flow_tracker.py --days 7
  python3 daily_flow_tracker.py --status ACTIVE PAUSED
  python3 daily_flow_tracker.py --gcs-bucket my-bucket # also upload outputs to GCS (see upload_to_gcs())
  python3 daily_flow_tracker.py --bigquery             # also write the BigQuery table (off by default -
                                                        # this service account can't create tables there yet,
                                                        # see write_bigquery()'s docstring; code kept ready for
                                                        # once that access is granted)

TWO REAL OUTPUTS, not one - see run():
  1. daily_flow_tracker.csv / .xlsx (override with --out-prefix) - "the
     latest pull", overwritten every run. This is what a caller wanting
     today's/current-state numbers should read.
  2. daily_flow_tracker_history.csv (override with --history-path) - every
     day's rows, UPSERTED (today's window's rows replace any older rows
     with the same real key, never duplicated; a genuinely new day just
     accumulates) - see write_history(). This is what gives periodic/
     multi-day/trend context instead of only ever one day at a time.
Safe to re-run any number of times per day - both outputs upsert cleanly,
neither duplicates rows on a repeat run for the same window."""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import moengage_client as mc  # noqa: E402

_DEFAULT_OUT_PREFIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_flow_tracker")
# Real, confirmed-live (2026-09-25) write access: ora-bigquery.ora_bigquery_
# pipeline - NOT crm-mail-automation-dev.crm_analytics_views (the original
# target - confirmed STILL denied, service account only has read there).
# One real, separate table PER real brand (moengage_daily_flow_tracker_
# <BRAND>, e.g. ..._AS_SG, ..._OVA_MY) - deliberately never one shared
# table, and deliberately a brand-new table name per brand, never reusing
# or overwriting any of the 49 real existing tables already in this
# dataset (checked live before ever writing here - see git history/session
# notes for the exact list confirmed clear of collisions).
_BQ_PROJECT_DATASET = "ora-bigquery.ora_bigquery_pipeline"
_DEFAULT_STATUS = None  # every real status - ACTIVE/PAUSED/STOPPED/RETIRED/DRAFT - no filtering by design


def _rows_for_flow(flow_meta: dict, brand: str = None) -> list:
    """One real flow's send (ACTION) nodes -> one row each, campaign_id
    still attached (Sent/Delivered/etc filled in later, once every
    matched flow's campaign_ids are known and fetched together in bulk -
    see build_tracker())."""
    flow_id = flow_meta["flow_id"]
    try:
        flow = mc.get_flow(flow_id, brand=brand)
    except Exception as exc:  # noqa: BLE001 - one flow failing shouldn't drop the rest
        print(f"  [skip] {flow_meta.get('name', flow_id)!r}: {exc}")
        return []

    rows = []
    for node in flow.get("structure", {}).get("nodes", []):
        if node.get("type") != "ACTION":
            continue
        campaign_id = (node.get("config") or {}).get("campaign_id")
        if not campaign_id:
            continue
        rows.append({
            "flow_id": flow_id, "flow_name": flow.get("name", flow_id), "flow_status": flow.get("status"),
            "node_label": node.get("label"), "channel": (node.get("config") or {}).get("channel"),
            "campaign_id": campaign_id,
        })
    return rows


_ACHANNEL_ENGAGEMENT_KEYS = ("open", "read", "impression")  # real per-channel meaning:
# EMAIL uses "open", WhatsApp uses "read", PUSH uses "impression" - confirmed live,
# no channel uses more than one of these, so summing across all three is safe (never
# double-counts within a single channel's own response).


_BLANK_VARIATION_STATS = {
    "attempted": None, "sent": None, "delivered": None, "sent_pct": None,
    "opened": None, "adjusted_opened": None, "clicked": None,
    "failed": None, "bounced": None, "unsubscribed": None, "complaints": None,
    "conversions": None, "conversions_unique": None, "cvr_pct": None, "revenue": None,
    "failure_reasons": None,
}


def _extract_variation_stats(variation_data: dict) -> dict:
    """Every field pulled here was confirmed to actually exist by dumping real
    raw Campaign Stats responses across EMAIL/WhatsApp/PUSH and unioning their
    real keys - not assumed from one channel's shape. Real, channel-dependent
    absences (no "delivered" concept for PUSH, no "failed"/bounce concept for
    WhatsApp, no adjusted_open outside EMAIL) are left genuinely blank, never
    guessed at. Operates on ONE real variation's own sub-dict (see
    _extract_by_variation below for which variations exist and why).

    Deliberately NOT pulled: MoEngage's own precomputed *_rate fields
    (open_rate, bounce_rate, ctr, ctor, adjusted_ctor, delivery_rate,
    failed_rate, unsub_rate, complaints_rate) - each is trivially re-derivable
    from the real counts already captured here (e.g. bounced/sent), so
    carrying both would just be redundant columns. Also not pulled: the
    deeper delivery_funnel breakdown (total_user_in_segment, after_fc,
    after_invalid_removal, user_with_email, etc.) - real audience-size/
    filtering diagnostics, useful for debugging a low Sent count, but not
    something this daily tracker's headline columns need."""
    out = dict(_BLANK_VARIATION_STATS)
    perf = variation_data.get("performance_stats") or {}

    for src, dst in (
        ("attempted", "attempted"), ("sent", "sent"), ("delivered", "delivered"),
        ("adjusted_open", "adjusted_opened"), ("click", "clicked"),
        ("failed", "failed"), ("bounce", "bounced"),
        ("unsubscribe", "unsubscribed"), ("complaint", "complaints"),
    ):
        if perf.get(src) is not None:
            out[dst] = (out[dst] or 0) + perf[src]
    for src in _ACHANNEL_ENGAGEMENT_KEYS:
        if perf.get(src) is not None:
            out["opened"] = (out["opened"] or 0) + perf[src]

    # MoEngage's own real "Sent %" for this campaign - confirmed live to
    # exist on EMAIL/PUSH campaigns' performance_stats (e.g. sent_rate:
    # 69.23), confirmed live to be genuinely ABSENT on WhatsApp
    # campaigns' performance_stats (no such field at all there - not a
    # bug, just not computed by MoEngage for that channel).
    if perf.get("sent_rate") is not None:
        out["sent_pct"] = perf["sent_rate"]

    for goal in (variation_data.get("conversion_goal_stats") or {}).values():
        if goal.get("total") is not None:
            out["conversions"] = (out["conversions"] or 0) + goal["total"]
        if goal.get("unique") is not None:
            out["conversions_unique"] = (out["conversions_unique"] or 0) + goal["unique"]
        if goal.get("revenue") is not None:
            out["revenue"] = (out["revenue"] or 0.0) + goal["revenue"]
        if goal.get("cvr") is not None:
            out["cvr_pct"] = goal["cvr"]

    # Real, named reasons sends failed (e.g. mo_engage_suppression,
    # f_c_removed, user_device_was_not_able_to_reproduce_the_content) -
    # confirmed live present and genuinely informative, not previously
    # captured at all.
    fb = variation_data.get("failure_breakdown") or {}
    if fb:
        out["failure_reasons"] = ", ".join(f"{k}={v}" for k, v in fb.items())
    return out


def _extract_by_variation(entry: dict) -> dict:
    """Real per-variation breakdown, keyed by MoEngage's own real variation
    name. Normally just "all_variations" (100% of the real audience this
    campaign sent to) - but a real "control_group" variation sits right
    alongside it in this same response for any campaign with a control
    group enabled (confirmed live: 6 real campaigns in this account) - a
    real, deliberately held-back slice of the audience that got NOTHING,
    kept by MoEngage as a genuine baseline for measuring incremental
    impact (uplift = all_variations' rate minus control_group's own rate
    on the same real conversion goal). Both are extracted with the exact
    same real fields (_extract_variation_stats); this function's only job
    is finding every real variation name present and summing each one's
    own numbers across this entry's platforms/locales - never blending
    the two variations' numbers together."""
    by_variation: dict = {}
    for platform in (entry.get("platforms") or {}).values():
        for locale in (platform.get("locales") or {}).values():
            for variation_name, variation_data in (locale.get("variations") or {}).items():
                stats = _extract_variation_stats(variation_data)
                target = by_variation.setdefault(variation_name, dict(_BLANK_VARIATION_STATS))
                for key, value in stats.items():
                    if value is None:
                        continue
                    if key in ("sent_pct", "cvr_pct"):
                        target[key] = value
                    elif key == "failure_reasons":
                        target[key] = value if key not in target or not target[key] else f"{target[key]}; {value}"
                    else:
                        target[key] = (target.get(key) or 0) + value
    return by_variation


def _automation_health_pct(row: dict):
    """The real "Automation health: % of active journeys firing without
    errors" metric from the War Room sheet's own written definition: "For
    Emails: Drops + Bounces, For Whatsapp: Sent - Delivered" - i.e. that
    definition names what counts as a FAILURE, so health % = 1 - failure/
    sent. EMAIL's "Drops" is read here as MoEngage's own real `failed`
    count (bounced is captured separately and added in). No rule was given
    for PUSH in that definition - left genuinely blank here rather than
    inventing one; ask before assuming PUSH should follow either rule.

    Real, live-confirmed failure mode (same class as the earlier Sent %
    issue): `failed` isn't bounded by that day's `sent` count - a retried
    send can fail on a later day than it was originally sent, so a 1-day
    window can show failed > sent and produce a nonsensical negative
    health % (confirmed live: 10 real EMAIL rows did exactly this).
    Rather than clamp/hide that with a fake 0%, this returns None (blank)
    whenever the inputs don't make sense, same honesty standard as
    targeted_reliable was for Sent % - a blank cell here means "this
    day's numbers don't support a clean answer," not "zero problems"."""
    sent = row.get("sent")
    if not sent:
        return None
    channel = (row.get("channel") or "").lower()
    if channel == "email":
        failures = (row.get("failed") or 0) + (row.get("bounced") or 0)
        if failures > sent:
            return None
        return round(100 * (1 - failures / sent), 1)
    if channel == "whatsapp":
        delivered = row.get("delivered")
        if delivered is None or delivered > sent:
            return None
        return round(100 * delivered / sent, 1)
    return None


def discover_base_rows(status: list, brand: str = None) -> list:
    """Real flow/node discovery ONLY - every real flow's every real ACTION
    node, no Campaign Stats fetched yet (see _fill_stats_for_window for
    that half). Split out from build_tracker() specifically so a backfill
    across many real days (see backfill_history()) pays this real cost -
    ~150 sequential get_flow calls, the slow part - exactly ONCE, not once
    per day backfilled. The flow/node shape doesn't meaningfully change
    day to day; only the real stats do."""
    flows = mc.list_all_flows(status=status, brand=brand)  # no category/name filter - every real flow, by design
    print(f"{len(flows)} real flow(s) found"
          f"{' (status filter: ' + ', '.join(status) + ')' if status else ' (every real status)'}:")
    for f in flows:
        print(f"  - {f['name']} ({f['status']})")

    all_rows = []
    for i, flow_meta in enumerate(flows, start=1):
        print(f"Flow {i}/{len(flows)}: {flow_meta['name']!r}")
        all_rows.extend(_rows_for_flow(flow_meta, brand=brand))
    return all_rows


_TRACKER_COLUMNS = [
    "brand", "flow_name", "flow_status", "node_label", "channel", "variation",
    "targeted", "attempted", "sent", "sent_pct",
    "delivered", "opened", "adjusted_opened", "clicked",
    "failed", "bounced", "unsubscribed", "complaints", "automation_health_pct", "failure_reasons",
    "conversions", "conversions_unique", "cvr_pct", "revenue",
    "date_range_start", "date_range_end", "moengage_workspace_id", "fetched_at", "flow_id", "campaign_id",
]


def _fill_stats_for_window(base_rows: list, start_str: str, end_str: str, workspace_id: str, brand: str = None) -> pd.DataFrame:
    """Real Campaign Stats for one specific real date window, merged onto
    a copy of the already-discovered base rows (see discover_base_rows) -
    the real per-window work, reused both by build_tracker() (today's one
    window) and backfill_history() (many real historical windows, one
    real discovery pass shared across all of them)."""
    if not base_rows:
        return pd.DataFrame(columns=_TRACKER_COLUMNS)

    campaign_ids = sorted({r["campaign_id"] for r in base_rows})
    print(f"Fetching real Campaign Stats for {len(campaign_ids)} send node(s), window {start_str}..{end_str}...")
    raw = mc.get_campaign_stats(campaign_ids, start_str, end_str, brand=brand)
    _RATE_FIELDS = ("sent_pct", "cvr_pct")  # MoEngage's own real rates - last non-None wins, never summed
    _TEXT_FIELDS = ("failure_reasons",)  # concatenated, never summed as a number
    # {campaign_id: {variation_name: stats}} - "all_variations" always the
    # main one; "control_group" only present for the real campaigns that
    # actually have one (confirmed live: 6 in this account) - see
    # _extract_by_variation()'s own docstring.
    variations_by_campaign: dict = {}
    for campaign_id, entries in raw.items():
        merged_variations: dict = {}
        for entry in entries:
            for variation_name, stats in _extract_by_variation(entry).items():
                merged = merged_variations.setdefault(variation_name, {})
                for key, value in stats.items():
                    if value is None:
                        continue
                    if key in _RATE_FIELDS:
                        merged[key] = value
                    elif key in _TEXT_FIELDS:
                        merged[key] = value if key not in merged else f"{merged[key]}; {value}"
                    else:
                        merged[key] = (merged.get(key) or 0) + value
        variations_by_campaign[campaign_id] = merged_variations

    fetched_at = datetime.now(timezone.utc).isoformat()
    final_rows = []
    control_group_rows = 0
    for base_row in base_rows:
        campaign_variations = variations_by_campaign.get(base_row["campaign_id"], {})
        # Real, live-confirmed MoEngage behavior: the "control_group" key is
        # structurally present on EVERY real campaign's response, whether or
        # not that campaign actually has a control group configured - for
        # the ones that don't, it's a real, permanently empty placeholder
        # (confirmed live: 0 of 528 had ANY non-null field in it on the
        # first unfiltered attempt at this). Only emit it as its own row
        # when it actually carries real data - a genuinely held-back
        # control group is usually small, so even a real one may show
        # nothing on a single narrow day, but an empty placeholder should
        # never turn into a blank row that just doubles the file.
        other_variations = [
            v for v in campaign_variations
            if v != "all_variations" and any(x is not None for x in campaign_variations[v].values())
        ]
        for variation_name in ["all_variations"] + other_variations:
            row = dict(base_row)
            row.update(campaign_variations.get(variation_name, {}))
            row["variation"] = variation_name
            # Deliberately blank - see module docstring: "Targeted" is a
            # stakeholder-set value, not a number MoEngage tracks. sent_pct,
            # unlike targeted, IS populated above where MoEngage provides its
            # own real sent_rate (EMAIL/PUSH - genuinely absent on WhatsApp,
            # left blank there rather than guessed).
            row["targeted"] = None
            row["automation_health_pct"] = _automation_health_pct(row)
            row["brand"] = brand or "AS_SG"
            row["moengage_workspace_id"] = workspace_id
            row["date_range_start"] = start_str
            row["date_range_end"] = end_str
            row["fetched_at"] = fetched_at
            final_rows.append(row)
            if variation_name != "all_variations":
                control_group_rows += 1

    if control_group_rows:
        print(f"{control_group_rows} real control_group row(s) added (campaigns with a real control group enabled).")

    return pd.DataFrame(final_rows, columns=_TRACKER_COLUMNS)


def build_tracker(days: int, status: list, brand: str = None) -> pd.DataFrame:
    """Ends YESTERDAY, not today - a real, deliberate fix: today's real
    MoEngage numbers are still accumulating (the day isn't over), so
    anchoring the window on "today" would make date_range_end a moving
    target that changes depending on what time of day this happens to
    run, and - the real bug this fixes - never lines up with
    backfill_history()'s clean single-calendar-day windows for the exact
    same real date (confirmed live: date_range_end differed by one day
    between a normal run and a backfilled day, so the history upsert's
    key never matched and silently created 1056 rows for one real date
    instead of replacing the existing 528). days=1 (the default) now
    means exactly ONE real calendar day (yesterday, start==end) - the
    same shape backfill_history() always used."""
    workspace_id = mc._creds_for_brand(brand)["workspace_id"] or "?"
    print(f"Listing every real flow in MoEngage workspace {workspace_id} (brand={brand or 'AS_SG'})...")
    base_rows = discover_base_rows(status, brand=brand)
    if not base_rows:
        print("No real send nodes found across the matched flows.")
        return pd.DataFrame(columns=_TRACKER_COLUMNS)

    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    return _fill_stats_for_window(base_rows, start_str, end_str, workspace_id, brand=brand)


def backfill_history(days_back: int, status: list, history_path: str, gcs_bucket: str = None, brand: str = None) -> pd.DataFrame:
    """Populates REAL past days into the history file directly, instead of
    waiting for the daily cron to accumulate them one real day at a time -
    real flow/node discovery happens ONCE here (the slow ~150-flow part),
    then one real, focused Campaign Stats window is pulled per real
    calendar day and upserted into history (see write_history() - a day
    already on record gets its real numbers refreshed, not duplicated).
    Each day is its own real 1-day window (date_range_start == date_range_
    end), exactly matching what the daily cron itself would have written
    for that real day - a backfilled day is indistinguishable from one the
    cron actually ran on. Returns the final real merged history DataFrame."""
    workspace_id = mc._creds_for_brand(brand)["workspace_id"] or "?"
    print(f"Listing every real flow in MoEngage workspace {workspace_id} (brand={brand or 'AS_SG'}, once, for the whole backfill)...")
    base_rows = discover_base_rows(status, brand=brand)
    if not base_rows:
        print("No real send nodes found - nothing to backfill.")
        return pd.DataFrame(columns=_TRACKER_COLUMNS)

    if gcs_bucket:
        download_history_from_gcs(gcs_bucket, history_path)

    today = datetime.now(timezone.utc).date()
    merged_history = None
    for offset in range(days_back, 0, -1):  # oldest real day first, so history reads chronologically as it builds
        day = today - timedelta(days=offset)
        day_str = day.strftime("%Y-%m-%d")
        print(f"\n--- Backfilling real day {day_str} ({days_back - offset + 1}/{days_back}) ---")
        df = _fill_stats_for_window(base_rows, day_str, day_str, workspace_id, brand=brand)
        if df.empty:
            continue
        merged_history = write_history(df, history_path)

    if merged_history is None:
        print("No real data returned for any backfilled day.")
        return pd.DataFrame(columns=_TRACKER_COLUMNS)

    if gcs_bucket:
        upload_to_gcs([history_path], gcs_bucket)

    print(f"\nBackfill complete: {days_back} real day(s) processed, {len(merged_history)} total row(s) now in history.")
    return merged_history


_DEFAULT_HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_flow_tracker_history.csv")
_HISTORY_KEY_COLUMNS = ["brand", "flow_id", "campaign_id", "variation", "date_range_start", "date_range_end"]


def write_history(df: pd.DataFrame, history_path: str) -> pd.DataFrame:
    """Appends today's real rows onto a growing historical file instead of
    overwriting it - this is what gives the analytics agent real multi-day/
    periodic context instead of only ever seeing one day. Upserted, not
    blindly appended: re-running for a window already recorded (same real
    flow_id/campaign_id/variation/date_range_start/date_range_end) REPLACES
    those old rows with today's fresh pull rather than duplicating them -
    a real re-run (e.g. after a bug fix) should correct history, not double
    it. A genuinely new day's rows are new keys, so they just accumulate.
    Returns the full merged history DataFrame (what actually got written)."""
    if os.path.exists(history_path):
        existing = pd.read_csv(history_path)
        if "brand" not in existing.columns:
            # Real migration case: every row written before multi-brand
            # support was added came from the one original workspace,
            # confirmed live to be andSons SG (AS_SG) - backfilling that
            # here rather than leaving these old rows with a blank/NaN
            # brand, which would break the key join below and silently
            # exclude them from every future brand-aware upsert/dedup.
            existing["brand"] = "AS_SG"
        # Drop any existing rows whose real key matches a row in today's
        # pull - today's version wins - then append today's rows fresh.
        key = lambda d: d[_HISTORY_KEY_COLUMNS].astype(str).agg("|".join, axis=1)  # noqa: E731
        existing = existing[~key(existing).isin(set(key(df)))]
        merged = pd.concat([existing, df], ignore_index=True)
    else:
        merged = df
    merged.to_csv(history_path, index=False)
    print(f"History updated: {len(merged)} total row(s) across all days in {history_path}")
    return merged


def download_history_from_gcs(bucket_name: str, history_path: str, prefix: str = "moengage_export") -> bool:
    """Pulls the real, growing history CSV down from GCS to the local
    history_path BEFORE write_history() runs - required because Cloud Run
    gives every run a fresh, empty local disk (see upload_to_gcs's own
    docstring), so without this, write_history()'s `os.path.exists(history_path)`
    check always sees "no local history" on Cloud Run and treats every
    single cron run as day one, silently overwriting the real multi-day
    history in GCS with just that one day's rows (confirmed live - this is
    exactly what happened on 2026-09-24: a stale-code run collapsed 7392
    real backfilled rows down to 528). Returns True if a real remote history
    was found and downloaded, False if there isn't one yet (first-ever run)
    or gcs_bucket is falsy - both real, non-error cases where write_history
    should just start fresh locally."""
    if not bucket_name:
        return False
    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(bucket_name).blob(f"{prefix}/{os.path.basename(history_path)}")
    if not blob.exists():
        return False
    blob.download_to_filename(history_path)
    print(f"Downloaded existing history from gs://{bucket_name}/{prefix}/{os.path.basename(history_path)} -> {history_path}")
    return True


def upload_to_gcs(local_paths: list, bucket_name: str, prefix: str = "moengage_export") -> None:
    """Uploads each real local file to gs://bucket/prefix/<basename> -
    Cloud Run's own local disk is NOT durable storage (a fresh instance or
    a redeploy starts with an empty filesystem - confirmed via Cloud Run's
    own documented execution model, not assumed), so anything a daily cron
    run produces that needs to survive past that one run/instance has to
    land somewhere durable. GCS is this project's real choice for that -
    reuses the same GOOGLE_APPLICATION_CREDENTIALS this script's BigQuery
    path already depends on, no separate credential to manage. Skipped
    entirely (returns without error) when bucket_name is falsy - GCS is
    opt-in, not required for local/dev use of this script."""
    if not bucket_name:
        return
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    for local_path in local_paths:
        blob_name = f"{prefix}/{os.path.basename(local_path)}"
        bucket.blob(blob_name).upload_from_filename(local_path)
        print(f"Uploaded {local_path} -> gs://{bucket_name}/{blob_name}")


#  =========================================================================
#  WAR ROOM REPLICA SHEET - a second, real-data-only view inside the same
#  .xlsx, shaped like the "CRM Daily" tab in Bryan's real "ORA Daily War
#  Room v2.xlsx" (one row per real day, the same 9 real metric columns -
#  confirmed against a direct read of that real file on 2026-09-24, not
#  from notes/memory: it's 9 metrics per brand, not 7 - "Cart abandonment
#  flow trigger rate %" and "Full abandonment sequence completion %" were
#  missing from an earlier version of this sheet and have been added).
#  Real, deliberate design choices, not oversights:
#
#  - TARGETS are copied VERBATIM from that real sheet's own "Target:" row
#    (_WAR_ROOM_TARGETS below) - these are the real stakeholder-set goal
#    values Bryan's team already chose, not derived from MoEngage at all
#    (per Bryan's own direct correction earlier - see module docstring).
#    NOTE: that real file's own Read Me tab says these targets were
#    "illustrative placeholders chosen by Claude" when the sheet was
#    first built, NOT sourced benchmarks - copied verbatim here because
#    that's still what's live on Bryan's sheet, but worth confirming with
#    Bryan whether his team has since set real ones.
#    His sheet has FOUR real brand/market target sets (Ova SG/ASSG/Ova MY/
#    AS MY); all four are copied here for reference since this script
#    genuinely cannot tell which brand/market a real flow belongs to (that
#    split lives in the SQL warehouse, not MoEngage - see module docstring)
#    - never silently pick just one and imply it's the only real target.
#  - ACTUAL VALUES are computed fresh from this script's own real history,
#    combined across whatever this one MoEngage workspace covers (no
#    brand split - same real limitation as the raw data sheet).
#  - Two of the seven real metrics genuinely cannot be computed from
#    MoEngage data at all (Payment abandon recovery rate needs payment-
#    retry data from the SQL warehouse; Segmented vs. batch-and-blast
#    share needs real campaign segmentation config from a different API,
#    Campaigns Search, not Campaign Stats) - left as real "N/A - see notes"
#    cells, never a fabricated number.
#  =========================================================================

# Copied verbatim from the real "ORA Daily War Room v2.xlsx" CRM Daily
# tab's own "Target:" row (read live 2026-09-24) - real stakeholder-set
# goals, not computed. Column order matches _WAR_ROOM_METRIC_ORDER below.
# The real Target row itself has NO value for "Cart abandonment flow
# trigger rate %"/"Full abandonment sequence completion %" (confirmed by
# reading the real file - those two columns are blank in row 6 for all 4
# real brand blocks) - None here, not a guessed number, reflects that.
# Keyed by this project's own canonical brand codes (same ones
# moengage_client.MOENGAGE_BRANDS and warehouse_client.BRANDS use) rather
# than Bryan's own display names, so this joins directly against the
# "brand" column build_war_room_sheet() now groups real values by.
# AS_PH/OVA_PH/MODERN_MOLECULES are real workspaces this project only
# gained MoEngage access to on 2026-09-24 - Bryan's real sheet has no
# Target row for them yet (it predates that access), so every metric is
# genuinely None here, not guessed at.
_WAR_ROOM_TARGETS = {
    "OVA_SG": [0.95, 0.95, 0.20, 0.95, None, None, 0.75, 0.50, 0.95],
    "AS_SG":  [0.95, 0.95, 0.10, 0.95, None, None, 0.75, 0.60, 0.95],
    "OVA_MY": [0.95, 0.95, 0.25, 0.95, None, None, 0.00, 0.40, 0.95],
    "AS_MY":  [0.95, 0.95, 0.10, 0.95, None, None, 0.00, 0.40, 0.95],
    "AS_PH":            [None] * 9,
    "OVA_PH":           [None] * 9,
    "MODERN_MOLECULES": [None] * 9,
}
_WAR_ROOM_METRIC_ORDER = [
    "Abandoned cart sent %", "Refill reminders sent %", "Payment abandon recovery rate",
    "Day-1 onboarding touch delivered", "Cart abandonment flow trigger rate %",
    "Full abandonment sequence completion %", "Consult-booked-but-not-attended win-back %",
    "Segmented vs. batch-and-blast share", "Automation health % (active journeys firing without errors)",
]
_WAR_ROOM_NOT_COMPUTABLE = {
    "Payment abandon recovery rate": "N/A - needs payment-retry data from the SQL warehouse, not in MoEngage",
    "Segmented vs. batch-and-blast share": "N/A - needs real campaign segmentation config (Campaigns Search API), not Campaign Stats",
    # Bryan's real sheet leaves this metric's own "How to measure" column
    # blank on the War Room tab (unlike every other CRM metric there) - no
    # agreed definition exists yet. The literal name implies comparing
    # real flow entries against the true count of carts abandoned, which
    # is ecommerce data MoEngage doesn't have (would need the SQL
    # warehouse/BigQuery) - genuinely not computable from MoEngage alone,
    # not just unimplemented.
    "Cart abandonment flow trigger rate %": "N/A - no agreed definition on Bryan's own sheet, and the literal reading (flow entries vs. true carts-abandoned count) needs ecommerce data MoEngage doesn't have",
}


def _flow_category(flow_name: str):
    """Real category keyword match, used ONLY for this summary sheet's
    grouping - the main data sheet/history stays fully unfiltered (every
    real flow, see module docstring's SCOPE section); this is an
    additional view built on top of that same complete data, not a
    narrower pull. Returns None for a flow that doesn't match any of the
    4 real War Room categories - those rows are simply excluded from
    THIS summary (they're still in the raw data sheet)."""
    lowered = (flow_name or "").lower()
    if "abandon" in lowered:
        return "Abandoned cart sent %"
    if "replenish" in lowered:
        return "Refill reminders sent %"
    if "onboard" in lowered:
        return "Day-1 onboarding touch delivered"
    if "no show" in lowered:
        return "Consult-booked-but-not-attended win-back %"
    return None


_UNRELIABLE_PCT = "unreliable this day (numerator > denominator - see chat/docs on the real attribution-window mismatch this causes)"


def _weighted_pct(numer_sum, denom_sum):
    """A real weighted rate - sum(numerator)/sum(denominator), never an
    average-of-percentages (which would mis-weight a low-volume node
    equally against a high-volume one). Same honesty rule as
    _automation_health_pct/Sent % elsewhere in this file: a numerator
    that exceeds its denominator is a REAL, confirmed-live symptom of
    MoEngage's attribution window not matching the send/report window
    (e.g. someone sent yesterday converting/counted today) - reported as
    unreliable, never as a fabricated >100% rate."""
    if not denom_sum:
        return None
    if numer_sum > denom_sum:
        return _UNRELIABLE_PCT
    return round(100 * numer_sum / denom_sum, 1)


def _flow_sequence_completion(cat_df: pd.DataFrame) -> tuple:
    """(numerator, denominator) = (sum of each real abandon-cart flow's
    LAST send-node's real sent count, sum of its FIRST send-node's real
    sent count), for one real day's abandon-cart rows. Bryan's own sheet
    has no "How to measure" definition for "Full abandonment sequence
    completion %" (confirmed - blank on the War Room tab, unlike every
    other CRM metric there), so this is a real, defensible reading of the
    metric's own name: of the users who entered a multi-step abandon-cart
    flow, what fraction made it through every step. "First"/"last" node
    here means first/last real ACTION node in that flow's own real node
    array, in the order _rows_for_flow() reads it from MoEngage's flow
    structure - never reordered afterward, including through history's
    upsert (which only ever drops/appends whole day-blocks, never
    reorders rows within one)."""
    numer = denom = 0
    for _flow_id, flow_rows in cat_df.groupby("flow_id", sort=False):
        sent_vals = flow_rows["sent"].tolist()
        if not sent_vals or pd.isna(sent_vals[0]) or pd.isna(sent_vals[-1]):
            continue
        denom += sent_vals[0]
        numer += sent_vals[-1]
    return numer, denom


def build_war_room_sheet(history_df: pd.DataFrame) -> pd.DataFrame:
    """One row per (real date, real brand) on record in history, the 9 real
    War Room metric columns - see the module-level block comment above for
    what's real-computed vs. a real, honest "N/A". Genuinely brand-split
    since 2026-09-24 (multi-workspace MoEngage access) - each brand's own
    workspace produces its own real numbers here, never blended with
    another brand's."""
    rows = []
    send_rows = history_df[(history_df["variation"] == "all_variations") & history_df["campaign_id"].notna()].copy()
    send_rows["category"] = send_rows["flow_name"].map(_flow_category)
    categorized = send_rows[send_rows["category"].notna()]

    for (date, brand), day_df in send_rows.groupby(["date_range_start", "brand"]):
        row = {"date": date, "brand": brand}
        day_categorized = categorized[(categorized["date_range_start"] == date) & (categorized["brand"] == brand)]
        for metric in _WAR_ROOM_METRIC_ORDER:
            if metric in _WAR_ROOM_NOT_COMPUTABLE:
                row[metric] = _WAR_ROOM_NOT_COMPUTABLE[metric]
            elif metric == "Automation health % (active journeys firing without errors)":
                # Spans the whole real CRM Daily scope (all 4 real
                # categories combined), not any one category - a real,
                # deliberate difference from the other 6 metrics below,
                # which are each scoped to their own one category.
                valid = day_categorized["automation_health_pct"].dropna()
                row[metric] = round(valid.mean(), 1) if not valid.empty else None
            elif metric == "Full abandonment sequence completion %":
                # Derived from the SAME abandon-cart flows as "Abandoned
                # cart sent %" above, not its own flow-name category -
                # there's no separate set of flows for this metric.
                cat_df = day_df[day_df["category"] == "Abandoned cart sent %"]
                if cat_df.empty:
                    row[metric] = None
                else:
                    numer, denom = _flow_sequence_completion(cat_df)
                    row[metric] = _weighted_pct(numer, denom)
            else:
                cat_df = day_df[day_df["category"] == metric]
                if cat_df.empty:
                    row[metric] = None
                elif metric == "Consult-booked-but-not-attended win-back %":
                    row[metric] = _weighted_pct(cat_df["conversions"].sum(), cat_df["sent"].sum())
                elif metric == "Day-1 onboarding touch delivered":
                    row[metric] = _weighted_pct(cat_df["delivered"].sum(), cat_df["sent"].sum())
                else:
                    row[metric] = _weighted_pct(cat_df["sent"].sum(), cat_df["attempted"].sum())
        rows.append(row)

    return pd.DataFrame(rows, columns=["date", "brand"] + _WAR_ROOM_METRIC_ORDER).sort_values(["date", "brand"])


def _war_room_target_reference_df() -> pd.DataFrame:
    """The real Target row(s), copied verbatim - see block comment above."""
    return pd.DataFrame(_WAR_ROOM_TARGETS, index=_WAR_ROOM_METRIC_ORDER).T.reset_index(names="brand/market")


# Real, human-readable sheet name per real MoEngage workspace this project
# has access to (2026-09-24) - Excel sheet names cap at 31 chars and can't
# hold : \ / ? * [ ], so this is a deliberate mapping, not just brand.title().
_BRAND_SHEET_NAMES = {
    "AS_SG": "andSons SG", "AS_MY": "andSons MY", "AS_PH": "andSons PH",
    "OVA_SG": "Ova SG", "OVA_MY": "Ova MY", "OVA_PH": "Ova PH",
    "MODERN_MOLECULES": "Modern Molecules",
}


def run(days: int, status: list, out_prefix: str, history_path: str, gcs_bucket: str = None) -> dict:
    """The real, importable entry point - same work main() does from the
    CLI, callable directly (e.g. from a Flask cron endpoint) without
    shelling out to a subprocess. Returns a small real summary dict, not
    the full DataFrame, so a caller like a cron HTTP handler has something
    cheap and JSON-safe to log/return.

    Multi-brand since 2026-09-24: loops over every real MoEngage workspace
    this project has credentials for (mc.MOENGAGE_BRANDS, filtered to the
    ones mc.brand_configured() confirms are actually set up - a brand
    added to .env later just starts working here next run, no code change
    needed), pulls each one's real data SEPARATELY (never blended - a
    failure or empty result on one brand doesn't drop the others), and
    writes each to its own real sheet in the same .xlsx, per Hari's own
    direct instruction (2026-09-24: "each workspace data should go into
    one sheet in the excel book")."""
    if gcs_bucket:
        download_history_from_gcs(gcs_bucket, history_path)

    per_brand_dfs = {}
    for brand in mc.MOENGAGE_BRANDS:
        if not mc.brand_configured(brand):
            continue
        print(f"\n=== Brand {brand} ===")
        try:
            df = build_tracker(days, status, brand=brand)
        except Exception as exc:  # noqa: BLE001 - one brand failing shouldn't drop the others
            print(f"  [skip brand {brand}] {exc}")
            continue
        if not df.empty:
            per_brand_dfs[brand] = df

    if not per_brand_dfs:
        return {"rows": 0, "message": "No real send nodes found across any configured brand - nothing written."}

    combined_df = pd.concat(per_brand_dfs.values(), ignore_index=True)
    csv_path, xlsx_path = f"{out_prefix}.csv", f"{out_prefix}.xlsx"
    combined_df.to_csv(csv_path, index=False)

    history_df = write_history(combined_df, history_path)

    # The .xlsx gets one real raw-data sheet PER real brand, plus the two
    # real War Room sheets - see the WAR ROOM REPLICA SHEET block comment
    # above for what those are and why.
    war_room_df = build_war_room_sheet(history_df)
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        for brand, df in per_brand_dfs.items():
            sheet_name = _BRAND_SHEET_NAMES.get(brand, brand)[:31]
            df.to_excel(writer, index=False, sheet_name=sheet_name)
        war_room_df.to_excel(writer, index=False, sheet_name="CRM Daily (real values)")
        _war_room_target_reference_df().to_excel(writer, index=False, sheet_name="CRM Daily (real targets)")
    sheet_list = ", ".join(_BRAND_SHEET_NAMES.get(b, b) for b in per_brand_dfs) + ", CRM Daily (real values), CRM Daily (real targets)"
    print(f"\nWrote {len(combined_df)} row(s) across {len(per_brand_dfs)} brand(s) to:\n  {csv_path}\n  {xlsx_path} (sheets: {sheet_list})")

    if gcs_bucket:
        upload_to_gcs([csv_path, xlsx_path, history_path], gcs_bucket)

    return {
        "rows": len(combined_df), "history_rows": len(history_df), "brands": list(per_brand_dfs.keys()),
        "csv_path": csv_path, "xlsx_path": xlsx_path, "history_path": history_path,
        "gcs_bucket": gcs_bucket,
    }


def write_bigquery(df: pd.DataFrame, table_id: str) -> bool:
    """Returns True on success. WRITE_TRUNCATE (full replace, not append) -
    correct here because the caller always passes the FULL current
    dataframe for that table (e.g. a brand's whole history, already
    deduped/upserted), so truncate-and-reload is simplest and avoids any
    real risk of duplicate rows from a repeat run. A permission failure
    here is reported plainly rather than crashing the whole run - the
    CSV/Excel output above already succeeded independent of this."""
    from google.cloud import bigquery
    from google.api_core.exceptions import Forbidden, NotFound

    project = table_id.split(".")[0]
    client = bigquery.Client(project=project)
    job_config = bigquery.LoadJobConfig(autodetect=True, write_disposition="WRITE_TRUNCATE")
    try:
        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
    except (Forbidden, NotFound) as exc:
        print(f"\nBigQuery write SKIPPED - {type(exc).__name__}: this service account lacks permission to "
              f"create/write {table_id!r}. CSV/Excel above are unaffected. Needs a real IAM grant "
              f"(bigquery.dataEditor or similar on this dataset) from whoever administers that GCP project - "
              f"not fixable from this script.\n  Real error: {exc}")
        return False
    table = client.get_table(table_id)
    print(f"Wrote {table.num_rows} row(s) to BigQuery table {table_id}")
    return True


def write_bigquery_per_brand(history_df: pd.DataFrame, project_dataset: str = _BQ_PROJECT_DATASET) -> dict:
    """The real historic (not just latest-day) data, one real table PER
    real brand (moengage_daily_flow_tracker_<BRAND>) - never one shared
    table, and never touching any table that isn't this script's own
    (every table name here is prefixed moengage_daily_flow_tracker_,
    checked live against every existing real table in the dataset before
    this was ever run - see _BQ_PROJECT_DATASET's own comment). Returns
    {brand: True/False} per real brand attempted."""
    results = {}
    for brand, brand_df in history_df.groupby("brand"):
        table_id = f"{project_dataset}.moengage_daily_flow_tracker_{brand}"
        print(f"\n--- Writing {len(brand_df)} historic row(s) for brand {brand} to {table_id} ---")
        results[brand] = write_bigquery(brand_df, table_id)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=1, help="Lookback window in days (default 1 - yesterday's numbers, matching Bryan's real routine).")
    parser.add_argument("--status", nargs="+", default=_DEFAULT_STATUS,
                         help=f"Real flow statuses to include (default {_DEFAULT_STATUS}).")
    parser.add_argument("--out-prefix", default=_DEFAULT_OUT_PREFIX,
                         help=f"Writes <prefix>.csv and <prefix>.xlsx (default {_DEFAULT_OUT_PREFIX}).")
    parser.add_argument("--history-path", default=_DEFAULT_HISTORY_PATH,
                         help=f"Growing multi-day history CSV, upserted not overwritten (default {_DEFAULT_HISTORY_PATH}).")
    parser.add_argument("--gcs-bucket", default=os.environ.get("MOENGAGE_EXPORT_GCS_BUCKET"),
                         help="GCS bucket to also upload outputs to (default: $MOENGAGE_EXPORT_GCS_BUCKET, unset = skip - see upload_to_gcs()'s docstring for why this matters on Cloud Run).")
    parser.add_argument("--bigquery", action="store_true",
                         help="Also write the FULL real history to BigQuery, one real table per real brand "
                              "(moengage_daily_flow_tracker_<BRAND> in --bq-project-dataset) - off by default, "
                              "see write_bigquery_per_brand()'s docstring.")
    parser.add_argument("--bq-project-dataset", default=_BQ_PROJECT_DATASET,
                         help=f"BigQuery project.dataset to write each brand's table into (default {_BQ_PROJECT_DATASET}).")
    parser.add_argument("--backfill-days", type=int, default=None,
                         help="Instead of the normal single-day run, populate this many REAL past days directly "
                              "into --history-path (one real Campaign Stats window per real day, flow discovery "
                              "done once - see backfill_history()). Does not touch --out-prefix's latest snapshot.")
    parser.add_argument("--brand", default=None, choices=mc.MOENGAGE_BRANDS,
                         help="Real MoEngage brand/workspace to backfill (see mc.MOENGAGE_BRANDS). Only used with "
                              "--backfill-days - the normal run() always covers every configured brand at once. "
                              "Default: every real configured brand, one after another.")
    args = parser.parse_args()

    if args.backfill_days:
        brands = [args.brand] if args.brand else [b for b in mc.MOENGAGE_BRANDS if mc.brand_configured(b)]
        for brand in brands:
            print(f"\n=== Backfilling brand {brand} ===")
            backfill_history(args.backfill_days, args.status, args.history_path, args.gcs_bucket, brand=brand)
        return

    result = run(args.days, args.status, args.out_prefix, args.history_path, args.gcs_bucket)
    if result["rows"] == 0:
        print(result["message"])
        return

    if args.bigquery:
        history_df = pd.read_csv(args.history_path)
        write_bigquery_per_brand(history_df, args.bq_project_dataset)


if __name__ == "__main__":
    main()

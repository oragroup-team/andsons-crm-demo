"""Fully automated, zero-manual-input dump of EVERY real flow in the
account, every node in every flow, into ONE Excel file and ONE SQLite
database - no --flow-id to look up first, no --search step, no picking a
branch at every fork. Run it, wait, get a spreadsheet/table covering the
whole account:

    python3 dump_all_flow_stats.py

That's the entire interface. `--days` (default 30) and `--status` (default:
every real status - ACTIVE/PAUSED/STOPPED/RETIRED/DRAFT) are optional
overrides, never required.

WHY THIS IS A SEPARATE SCRIPT FROM flow_node_stats_to_db.py, NOT A LOOP
AROUND IT: that script also approximates each flow's own Entered/Drops
numbers via the Funnels Query API (one async, polled query PER PATH
through the flow). That's fine for one flow picked by hand, but doesn't
scale to "every flow, unattended": 150 real flows confirmed live in this
workspace, several paths each, each funnel query taking several real
seconds to resolve - a full account run would take a genuinely long time
AND some flows' real trigger schema (ATTRIBUTE-triggered vs EVENT-
triggered, etc.) isn't confirmed to feed the Funnels API cleanly the way
the EVENT-triggered flows tested so far do. So this script deliberately
leaves the Funnels API out entirely and reports ONLY the numbers that are
exact, fast, and reliable at full account scale: real Campaign Stats
(get_campaign_stats) for every ACTION node's real campaign_id, across
EVERY flow, in bulk. If you need one specific flow's own approximate
Entered/Drops numbers too, run flow_node_stats_to_db.py for that one
flow_id - it does the slower per-flow funnel work this script skips.

As a stand-in for "Entered" on action nodes specifically, this reports the
real `attempted` figure from Campaign Stats (users MoEngage actually tried
to send that step to) - close in spirit to a canvas "Entered" count for
that one send, though not identical (MoEngage's own real definitions, not
assumed to be interchangeable). Structural nodes (TRIGGER/CONDITION/
BRANCH/SPLIT/CONTROL) get a row for context/shape but no Entered number -
there's no live, reliable, account-wide way to get that without the
Funnels API tradeoffs above.

OUTPUT: flow_stats_dump.xlsx and flow_stats_dump.db (both default to this
script's own folder, override with --xlsx / --db) - one flat row per node
per flow, every flow in the account. Each full run REPLACES both files
outright (not upserted/appended) - this is a full account snapshot each
time, not an incremental log.
"""
import argparse
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import moengage_client as mc  # noqa: E402
from flow_node_stats_to_db import build_leaf_paths, _path_label, merge_campaign_stats  # noqa: E402

_DEFAULT_XLSX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_stats_dump.xlsx")
_DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_stats_dump.db")
_GET_FLOW_WORKERS = 6  # same concurrency moengage_client.py itself uses elsewhere for this workspace's real scale

_COLUMNS = [
    "flow_id", "flow_name", "flow_status", "flow_version",
    "node_stage_id", "node_label", "node_type", "node_sub_type", "path_label",
    "campaign_id", "channel",
    "attempted", "sent", "delivered", "opened", "adjusted_opened", "clicked", "conversions", "revenue",
    "date_range_start", "date_range_end", "fetched_at",
]


def _rows_for_flow(flow_meta: dict) -> tuple:
    """Fetches one real flow's structure and returns (rows, campaign_ids)
    - rows have every metric column left blank for now (filled in later,
    once every flow's campaign_ids are known and fetched together in bulk
    - see main()). One bad flow (deleted mid-run, permission change)
    doesn't abort the whole account dump - it's logged and skipped."""
    flow_id = flow_meta["flow_id"]
    try:
        flow = mc.get_flow(flow_id)
    except Exception as exc:  # noqa: BLE001 - one flow failing shouldn't drop the rest of the account
        print(f"  [skip] {flow_meta.get('name', flow_id)!r}: {exc}")
        return [], []

    nodes = flow.get("structure", {}).get("nodes", [])
    nodes_by_id = {n["stage_id"]: n for n in nodes}
    trigger = next((n for n in nodes if n["type"] == "TRIGGER"), None)
    if trigger is None:
        return [], []

    paths = build_leaf_paths(nodes_by_id, trigger["stage_id"])
    nodes_seen = {}
    for path in paths:
        for i, node in enumerate(path):
            nodes_seen.setdefault(node["stage_id"], (node, _path_label(path, i)))

    rows = []
    campaign_ids = []
    for stage_id, (node, path_label) in nodes_seen.items():
        campaign_id = (node.get("config") or {}).get("campaign_id")
        if campaign_id:
            campaign_ids.append(campaign_id)
        rows.append({
            "flow_id": flow_id, "flow_name": flow.get("name", flow_id),
            "flow_status": flow.get("status"), "flow_version": flow.get("active_version_name"),
            "node_stage_id": stage_id, "node_label": node.get("label"),
            "node_type": node.get("type"), "node_sub_type": node.get("sub_type"),
            "path_label": path_label, "campaign_id": campaign_id,
            "channel": (node.get("config") or {}).get("channel"),
        })
    return rows, campaign_ids


def dump_all(days: int, status: list, xlsx_path: str, db_path: str) -> None:
    print("Listing every real flow in the account...")
    flows = mc.list_all_flows(status=status)
    print(f"Found {len(flows)} real flow(s){' (status filter: ' + ', '.join(status) + ')' if status else ''}.")

    all_rows = []
    all_campaign_ids = set()
    print(f"Fetching each flow's real structure ({_GET_FLOW_WORKERS}-way parallel)...")
    with ThreadPoolExecutor(max_workers=_GET_FLOW_WORKERS) as pool:
        futures = {pool.submit(_rows_for_flow, f): f for f in flows}
        done = 0
        for future in as_completed(futures):
            rows, campaign_ids = future.result()
            all_rows.extend(rows)
            all_campaign_ids.update(campaign_ids)
            done += 1
            if done % 25 == 0 or done == len(flows):
                print(f"  ...{done}/{len(flows)} flows processed")

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    stats_by_campaign = {}
    if all_campaign_ids:
        campaign_ids = sorted(all_campaign_ids)
        print(f"Fetching real Campaign Stats for {len(campaign_ids)} distinct action node(s) across the whole account...")
        raw = mc.get_campaign_stats(campaign_ids, start_str, end_str)
        stats_by_campaign = {campaign_id: merge_campaign_stats(entries) for campaign_id, entries in raw.items()}

    fetched_at = datetime.now(timezone.utc).isoformat()
    for row in all_rows:
        action_stats = stats_by_campaign.get(row["campaign_id"], {}) if row["campaign_id"] else {}
        row["attempted"] = action_stats.get("attempted")
        row["sent"] = action_stats.get("sent")
        row["delivered"] = action_stats.get("delivered")
        row["opened"] = action_stats.get("opened")
        row["adjusted_opened"] = action_stats.get("adjusted_opened")
        row["clicked"] = action_stats.get("clicked")
        row["conversions"] = action_stats.get("conversions")
        row["revenue"] = action_stats.get("revenue")
        row["date_range_start"] = start_str
        row["date_range_end"] = end_str
        row["fetched_at"] = fetched_at

    df = pd.DataFrame(all_rows, columns=_COLUMNS)

    df.to_excel(xlsx_path, index=False, sheet_name="flow_node_stats")
    print(f"Wrote {len(df)} row(s) to {xlsx_path}")

    conn = sqlite3.connect(db_path)
    df.to_sql("flow_node_stats", conn, if_exists="replace", index=False)
    conn.close()
    print(f"Wrote {len(df)} row(s) to {db_path} (table flow_node_stats)")

    print(f"\nDone - {len(flows)} flow(s), {len(df)} node row(s), window {start_str}..{end_str}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days for Campaign Stats (default 30).")
    parser.add_argument("--status", nargs="+", default=None,
                         help="Only these real flow statuses (e.g. --status ACTIVE PAUSED). Default: every status.")
    parser.add_argument("--xlsx", default=_DEFAULT_XLSX_PATH, help=f"Excel file to write (default {_DEFAULT_XLSX_PATH}).")
    parser.add_argument("--db", default=_DEFAULT_DB_PATH, help=f"SQLite file to write (default {_DEFAULT_DB_PATH}).")
    args = parser.parse_args()
    dump_all(args.days, args.status, args.xlsx, args.db)


if __name__ == "__main__":
    main()

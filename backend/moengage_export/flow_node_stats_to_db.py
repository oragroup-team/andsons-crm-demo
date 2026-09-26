"""Pulls a real MoEngage flow's per-node numbers - both halves of what the
MoEngage UI's flow canvas shows - into a real SQLite database, one row per
node, instead of printing to a console. Built directly off two real, live-
confirmed API findings:

  1. Entered/Drops-style step counts (TRIGGER/CONDITION nodes): no MoEngage
     API returns these at all - see moengage_client.get_flow()'s docstring
     and flow_funnel_approximation.py, which this script reuses the walking
     logic from. They're APPROXIMATED here via the Funnels Query API, same
     caveats as that script (funnel semantics != a flow's own timing/
     re-entry rules - a comparison tool, not a source of truth). Unlike
     flow_funnel_approximation.py, this walks EVERY path through the flow
     (not one path chosen interactively) - see build_leaf_paths() - since a
     DB-format deliverable needs every branch's numbers, not just one.

  2. Sent/Delivered/Opened/Clicked/Conversions/Revenue (ACTION nodes): these
     ARE real, exact numbers - confirmed live via the separate Campaign
     Stats API (moengage_client.get_campaign_stats), keyed by the real
     campaign_id every ACTION node already carries in the flow's own
     structure. No approximation involved for this half.

USAGE
  python3 flow_node_stats_to_db.py --flow-id 679c77b30df1fe2a7817e33a --days 30
  python3 flow_node_stats_to_db.py --search "Abandon Cart"   # find a flow_id first

Writes/updates backend/moengage_export/flow_node_stats.db (override with
--db). Safe to re-run: rows are upserted (flow_id, node_stage_id,
date_range_start, date_range_end), not appended, so re-running for the same
flow/window refreshes numbers in place rather than duplicating rows.
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import moengage_client as mc  # noqa: E402

_DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_node_stats.db")
_FUNNEL_STEP_TYPES = {"TRIGGER", "CONDITION"}
_PASS_THROUGH_TYPES = {"BRANCH", "SPLIT", "ACTION"}


def build_leaf_paths(nodes_by_id: dict, start_stage_id: str) -> list:
    """Every real root-to-leaf path through a flow's structure - unlike
    flow_funnel_approximation.py's single interactively-chosen path, this
    walks ALL of them (a DB export needs every branch's own numbers, not
    just one human-picked arm). A "leaf" is a real EXIT node, or simply a
    node with no children (a dead end in the structure). Cycle guard: a
    GO_TO node that re-converges back into a node already on THIS path
    stops the walk there rather than looping - it's a real re-convergence
    point, not a genuinely new path, so it's recorded once as a leaf."""
    paths = []

    def dfs(stage_id, current_path, visited):
        if stage_id is None or stage_id in visited:
            if current_path:
                paths.append(current_path)
            return
        node = nodes_by_id.get(stage_id)
        if node is None:
            if current_path:
                paths.append(current_path)
            return
        current_path = current_path + [node]
        visited = visited | {stage_id}
        if node.get("type") == "CONTROL" and node.get("sub_type") == "EXIT":
            paths.append(current_path)
            return
        children = node.get("child_stage_ids") or []
        if not children:
            paths.append(current_path)
            return
        for child_id in children:
            dfs(child_id, current_path, visited)

    dfs(start_stage_id, [], set())
    return paths


def _path_label(path: list, upto_index: int) -> str:
    """A human-readable breadcrumb from the flow's entry down to one node
    in the path, e.g. 'Flow entry > Has done event? > Branch 2 > Copy -
    AC_Email 1' - so a node reached only via one particular branch reads
    unambiguously in the DB, matching how a human would describe it while
    looking at the real MoEngage canvas."""
    labels = [n.get("label") or n.get("sub_type") or n.get("type") for n in path[: upto_index + 1]]
    return " > ".join(labels)


def _funnel_steps_for_path(path: list) -> list:
    """The real TRIGGER/CONDITION event filters along one path, in order -
    same extraction flow_funnel_approximation.py uses, just applied to a
    fixed path instead of one built interactively."""
    steps = []
    for node in path:
        node_type = node.get("type")
        if node_type == "TRIGGER":
            # Real, live-confirmed shape: config.trigger.primary_conditions.filters
            # - NOT config.trigger.event.filters (that key doesn't exist).
            trigger = (node.get("config") or {}).get("trigger", {}).get("primary_conditions", {})
            steps.append({"stage_id": node["stage_id"], "filters": trigger.get("filters", [])})
        elif node_type == "CONDITION":
            condition = (node.get("config") or {}).get("condition", {})
            steps.append({"stage_id": node["stage_id"], "filters": condition.get("filters", [])})
    return steps


def _to_funnel_event(step_number: int, step: dict) -> dict:
    return {
        "from_step": None, "to_step": None, "step_type": "include", "step_number": step_number,
        "c_at_trigger_seg_v2": {"included_filters": {"filter_operator": "and", "filters": step["filters"]}},
        "c_at_act_seg_v2": {"included_filters": {"filter_operator": "and", "filters": []}},
    }


def _funnel_payload(events: list, days: int) -> dict:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return {
        "events": events,
        "segmentation": [{"filters": {"included_filters": {"filter_operator": "and", "filters": [
            {"id": "moe_all_users", "name": "All Users", "filter_type": "custom_segments"}
        ]}}}],
        "funnel_type": "user_funnel",
        "timerange": {
            "start": start.strftime("%Y-%m-%d %H:%M:%S"), "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "label": f"Last {days} Days", "dt_label": "last", "value": days,
        },
        "grouped_by_meta": {}, "grouped_by": [], "holding_attributes_meta": {}, "holding_attributes": [],
        "funnel_window": 86400 * days, "funnel_window_multiplier": 86400, "strict_order": False,
        "distribution": {}, "countType": "number", "showConversionEventOnly": False, "chart_type": "bar",
        "comparison_timerange": {}, "version": "2.0", "type": "funnel", "granularity": "e",
    }


def entered_counts_for_path(path: list, days: int, retries: int = 1) -> dict:
    """Runs one real Funnels query for one path's TRIGGER/CONDITION steps,
    returns {stage_id: entered_count}. Skipped (returns {}) for a path with
    no funnel-representable steps at all (shouldn't happen - every path
    starts at the flow's own TRIGGER - but guarded rather than assumed).

    Real, live-confirmed behavior: a genuinely high share of real Funnels
    queries come back FAILED with no further detail from MoEngage's own
    status endpoint (confirmed live: a brand-new flow with ~zero real
    event history, or a narrow 1-day window, both do this) - `retries`
    gives each path one real second attempt in case it's transient, but
    this is NOT assumed to fix a systematic case (e.g. genuinely zero
    events in the window) - a path that fails on every attempt still
    returns {} and is reported, not silently retried forever."""
    steps = _funnel_steps_for_path(path)
    if not steps:
        return {}
    events = [_to_funnel_event(i, s) for i, s in enumerate(steps, start=1)]
    last_exc = None
    for attempt in range(retries + 1):
        try:
            results = mc.run_funnel_query(_funnel_payload(events, days))
            break
        except Exception as exc:  # noqa: BLE001 - one path's funnel failing shouldn't drop the rest
            last_exc = exc
            if attempt < retries:
                time.sleep(2)
    else:
        print(f"  Funnel query failed for one path: {last_exc}")
        return {}
    by_step = {}
    for row in results:
        by_step.setdefault(row["step"], []).append(row["metric"])
    return {
        steps[i - 1]["stage_id"]: sum(m for m in metrics if isinstance(m, (int, float)))
        for i, metrics in by_step.items()
        if 1 <= i <= len(steps)
    }


def _extract_action_stats(entry: dict) -> dict:
    """Pulls the real numbers out of one campaign-stats response entry's
    real nested shape (platforms -> locales -> variations ->
    all_variations). Sums conversion_goal_stats' revenue/conversions
    across every real goal defined on the campaign - a campaign can have
    more than one conversion goal, and this script reports the campaign's
    total, not any one goal by name."""
    stats = {"attempted": None, "sent": None, "delivered": None, "opened": None, "adjusted_opened": None,
              "clicked": None, "conversions": None, "revenue": None}
    for platform in (entry.get("platforms") or {}).values():
        for locale in (platform.get("locales") or {}).values():
            variations = locale.get("variations") or {}
            all_var = variations.get("all_variations") or {}
            perf = all_var.get("performance_stats") or {}
            for src_key, dst_key in (("attempted", "attempted"), ("sent", "sent"), ("delivered", "delivered"),
                                      ("open", "opened"), ("adjusted_open", "adjusted_opened"), ("click", "clicked"),
                                      ("read", "opened")):  # WhatsApp uses "read" where email uses "open"
                if src_key in perf and perf[src_key] is not None:
                    stats[dst_key] = (stats[dst_key] or 0) + perf[src_key]
            for goal in (all_var.get("conversion_goal_stats") or {}).values():
                if goal.get("total") is not None:
                    stats["conversions"] = (stats["conversions"] or 0) + goal["total"]
                if goal.get("revenue") is not None:
                    stats["revenue"] = (stats["revenue"] or 0.0) + goal["revenue"]
    return stats


def merge_campaign_stats(entries: list) -> dict:
    """One real campaign_id's list of campaign-stats entries (more than
    one only when get_campaign_stats() had to chunk across a >30-day
    range) -> one summed stats dict. Shared by every caller that walks
    mc.get_campaign_stats()'s real {campaign_id: [entries]} response."""
    merged = {"attempted": None, "sent": None, "delivered": None, "opened": None, "adjusted_opened": None,
              "clicked": None, "conversions": None, "revenue": None}
    for entry in entries:
        for key, value in _extract_action_stats(entry).items():
            if value is not None:
                merged[key] = (merged[key] or 0) + value
    return merged


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flow_node_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flow_id TEXT NOT NULL,
            flow_name TEXT,
            node_stage_id TEXT NOT NULL,
            node_label TEXT,
            node_type TEXT,
            node_sub_type TEXT,
            path_label TEXT,
            campaign_id TEXT,
            channel TEXT,
            entered_approx INTEGER,
            attempted INTEGER,
            sent INTEGER,
            delivered INTEGER,
            opened INTEGER,
            adjusted_opened INTEGER,
            clicked INTEGER,
            conversions INTEGER,
            revenue REAL,
            date_range_start TEXT NOT NULL,
            date_range_end TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            UNIQUE(flow_id, node_stage_id, date_range_start, date_range_end)
        )
    """)
    conn.commit()


def export_flow(flow_id: str, days: int, db_path: str) -> None:
    flow = mc.get_flow(flow_id)
    flow_name = flow.get("name", flow_id)
    print(f"Flow: {flow_name} ({flow.get('active_version_name', '?')}, status {flow.get('status')})")

    nodes = flow.get("structure", {}).get("nodes", [])
    nodes_by_id = {n["stage_id"]: n for n in nodes}
    trigger = next((n for n in nodes if n["type"] == "TRIGGER"), None)
    if trigger is None:
        print("No TRIGGER node found in this flow's structure - nothing to export.")
        return

    paths = build_leaf_paths(nodes_by_id, trigger["stage_id"])
    print(f"Walked {len(paths)} real path(s) through this flow's structure (every branch, not one chosen path).")

    # One node can appear on more than one path (a shared prefix before a
    # fork) - keep the FIRST path it's seen on for its breadcrumb label,
    # and de-duplicate everything else by stage_id.
    nodes_seen = {}
    for path in paths:
        for i, node in enumerate(path):
            nodes_seen.setdefault(node["stage_id"], (node, _path_label(path, i)))

    entered_by_stage = {}
    for i, path in enumerate(paths, start=1):
        print(f"  Running funnel approximation for path {i}/{len(paths)}...")
        entered_by_stage.update(entered_counts_for_path(path, days))

    action_nodes = {
        stage_id: node for stage_id, (node, _) in nodes_seen.items()
        if node.get("type") == "ACTION" and (node.get("config") or {}).get("campaign_id")
    }
    campaign_ids = [n["config"]["campaign_id"] for n in action_nodes.values()]

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    stats_by_campaign = {}
    if campaign_ids:
        print(f"Fetching real Campaign Stats for {len(campaign_ids)} action node(s)...")
        raw = mc.get_campaign_stats(campaign_ids, start_str, end_str)
        stats_by_campaign = {campaign_id: merge_campaign_stats(entries) for campaign_id, entries in raw.items()}

    fetched_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    _ensure_schema(conn)
    rows_written = 0
    for stage_id, (node, path_label) in nodes_seen.items():
        campaign_id = (node.get("config") or {}).get("campaign_id")
        action_stats = stats_by_campaign.get(campaign_id, {}) if campaign_id else {}
        conn.execute(
            """INSERT INTO flow_node_stats (
                flow_id, flow_name, node_stage_id, node_label, node_type, node_sub_type,
                path_label, campaign_id, channel, entered_approx, attempted,
                sent, delivered, opened, adjusted_opened, clicked, conversions, revenue,
                date_range_start, date_range_end, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(flow_id, node_stage_id, date_range_start, date_range_end) DO UPDATE SET
                flow_name=excluded.flow_name, node_label=excluded.node_label,
                node_type=excluded.node_type, node_sub_type=excluded.node_sub_type,
                path_label=excluded.path_label, campaign_id=excluded.campaign_id,
                channel=excluded.channel, entered_approx=excluded.entered_approx,
                attempted=excluded.attempted,
                sent=excluded.sent, delivered=excluded.delivered, opened=excluded.opened,
                adjusted_opened=excluded.adjusted_opened, clicked=excluded.clicked,
                conversions=excluded.conversions, revenue=excluded.revenue,
                fetched_at=excluded.fetched_at
            """,
            (
                flow_id, flow_name, stage_id, node.get("label"), node.get("type"), node.get("sub_type"),
                path_label, campaign_id, (node.get("config") or {}).get("channel"),
                entered_by_stage.get(stage_id), action_stats.get("attempted"),
                action_stats.get("sent"), action_stats.get("delivered"), action_stats.get("opened"),
                action_stats.get("adjusted_opened"), action_stats.get("clicked"),
                action_stats.get("conversions"), action_stats.get("revenue"),
                start_str, end_str, fetched_at,
            ),
        )
        rows_written += 1
    conn.commit()
    conn.close()
    print(f"Wrote {rows_written} node row(s) to {db_path} (table flow_node_stats, window {start_str}..{end_str}).")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--search", help="List real flows whose name contains this text, with their flow_id.")
    parser.add_argument("--flow-id", help="Export node stats for this real flow_id.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default 30, max 30 per underlying funnel call).")
    parser.add_argument("--db", default=_DEFAULT_DB_PATH, help=f"SQLite file to write to (default {_DEFAULT_DB_PATH}).")
    args = parser.parse_args()

    if args.search:
        flows = mc.search_flows(name=args.search, limit=20)
        if not flows:
            print(f"No real flows found matching {args.search!r}.")
            return
        print(f"{len(flows)} real flow(s) matching {args.search!r}:")
        for f in flows:
            print(f"  {f['flow_id']}  {f['name']}  ({f['status']}, {f.get('active_version_name', '?')})")
        return

    if args.flow_id:
        export_flow(args.flow_id, args.days, args.db)
        return

    parser.print_help()


if __name__ == "__main__":
    main()

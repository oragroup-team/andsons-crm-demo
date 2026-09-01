"""Approximates a real MoEngage flow's own node-level stats (the
"Entered/Current/Drops/Exits" numbers shown per stage in the MoEngage
UI's flow canvas) using the Funnels Query API - because, confirmed
directly against MoEngage's real, published API spec, no MoEngage API
returns those numbers at all. The Flows API (moengage_client.get_flow)
gives you a flow's real STRUCTURE (nodes, branching, each condition's
real event filters) but zero stats fields anywhere; the Funnels API runs
a real analysis but only over an event sequence YOU define - it has no
concept of "flow X's own canvas". This script bridges the two: it reads
a flow's real trigger/condition events straight out of its own structure
and feeds them into a Funnels query, so the resulting step counts
approximate what the canvas would show - approximate, not identical,
since a funnel computes conversion from raw event history over a
date range you choose, while the flow's own canvas tracks real users
actually moving through that exact flow (timing/wait windows, exit
conditions, and re-entry rules all differ in ways a funnel can't fully
replicate). Built to let a human compare the two side by side and decide
how close they land, not to replace looking at the flow.

REAL, LIVE-CONFIRMED LIMITATION - branching: many real flows split
(Intelligent Path Optimizer, A/B tests, conditional branches) - a Funnels
query is a strict linear sequence, so it can't represent a fork. This
script walks a flow's structure from its TRIGGER node through CONDITION
nodes only (the real decision points a canvas step actually is), and
STOPS the moment it hits a SPLIT/BRANCH node, reporting how far it got
rather than guessing which branch to follow. Action nodes (an actual
campaign send) are skipped - MoEngage doesn't expose a generic
"delivered" event name to query safely, and every real canvas step
Bryan asked about was itself a CONDITION/wait, not a send.

USAGE
  List flows matching a name (find the real flow_id you want):
    python3 flow_funnel_approximation.py --search "Abandon Cart"

  Run the approximation for one real flow, last 30 days:
    python3 flow_funnel_approximation.py --flow-id 69311e8a03540c15feb4a63a --days 30

Prints the funnel step-by-step, labelled with each real node's own label
from the flow builder (e.g. "Made purchase?") - hold this up against the
same flow's real canvas in the MoEngage UI to see how close they land.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import moengage_client as mc  # noqa: E402


def _walk_to_funnel_events(nodes_by_id: dict, start_stage_id: str) -> tuple:
    """Walks a flow's real structure from its TRIGGER node, collecting
    every TRIGGER/CONDITION node's real event filter as one funnel step,
    in order. Stops at the first SPLIT/BRANCH/ACTION/CONTROL(non-wait)
    node it can't linearly represent. Returns (steps, stopped_reason) -
    steps is a list of {"label", "filters"} ready to become Funnels API
    event entries; stopped_reason is None if the walk reached a real dead
    end (EXIT) cleanly, or a real one-line explanation otherwise."""
    steps = []
    stage_id = start_stage_id
    seen = set()
    while stage_id and stage_id not in seen:
        seen.add(stage_id)
        node = nodes_by_id.get(stage_id)
        if node is None:
            return steps, f"Reached an unknown stage_id {stage_id!r} - stopping."

        node_type = node.get("type")
        if node_type == "TRIGGER":
            event = (node.get("config") or {}).get("trigger", {}).get("event", {})
            steps.append({"label": node.get("label") or "Flow entry", "filters": event.get("filters", [])})
        elif node_type == "CONDITION":
            condition = (node.get("config") or {}).get("condition", {})
            steps.append({"label": node.get("label") or node.get("sub_type") or "Condition", "filters": condition.get("filters", [])})
        elif node_type == "CONTROL" and node.get("sub_type") in ("WAIT_FOR_TIMER", "GO_TO"):
            pass  # a real delay/re-convergence, not an event - no funnel step, just keep walking
        elif node_type == "CONTROL" and node.get("sub_type") == "EXIT":
            return steps, None  # a clean, real end of this linear path
        elif node_type == "ACTION":
            pass  # a real campaign send - no safe generic "delivered" event name to query, skip
        else:
            return steps, f"Flow branches here (a real {node_type}/{node.get('sub_type')} node, {node.get('label') or 'unlabeled'}) - this script only follows a single linear path."

        children = node.get("child_stage_ids") or []
        if len(children) > 1:
            return steps, f"Flow branches here (after {node.get('label') or node_type}, {len(children)} real paths) - this script only follows a single linear path."
        stage_id = children[0] if children else None

    return steps, None


def _to_funnel_event(step_number: int, step: dict) -> dict:
    """One walked step -> one real Funnels API event entry. Reuses the
    step's own real filters directly (Flows and Funnels share the same
    real filter grammar - confirmed live, not assumed: a flow's own
    trigger/condition filters worked as Funnels `included_filters` with
    zero translation)."""
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


def approximate_flow(flow_id: str, days: int) -> None:
    flow = mc.get_flow(flow_id)
    print(f"Flow: {flow['name']} ({flow.get('active_version_name', '?')}, status {flow.get('status')})")

    nodes = flow.get("structure", {}).get("nodes", [])
    nodes_by_id = {n["stage_id"]: n for n in nodes}
    trigger = next((n for n in nodes if n["type"] == "TRIGGER"), None)
    if trigger is None:
        print("No TRIGGER node found in this flow's structure - nothing to approximate.")
        return

    steps, stopped_reason = _walk_to_funnel_events(nodes_by_id, trigger["stage_id"])
    if not steps:
        print("Couldn't build any real funnel steps from this flow's structure.")
        return

    print(f"\nWalked {len(steps)} real step(s) from this flow's own structure:")
    for i, s in enumerate(steps, start=1):
        print(f"  {i}. {s['label']}")
    if stopped_reason:
        print(f"\nStopped early: {stopped_reason}")
        print("(Steps found before the branch are still run below - a real, partial approximation, not a guess past that point.)")

    events = [_to_funnel_event(i, s) for i, s in enumerate(steps, start=1)]
    print(f"\nRunning the funnel over the last {days} day(s)...")
    try:
        results = mc.run_funnel_query(_funnel_payload(events, days))
    except Exception as exc:  # noqa: BLE001 - surfaced plainly, this is a manual comparison tool
        print(f"Funnel query failed: {exc}")
        return

    by_step = {}
    for row in results:
        by_step.setdefault(row["step"], []).append(row["metric"])

    print("\nApproximate funnel results (compare against the real canvas in the MoEngage UI):")
    prev_metric = None
    for i, s in enumerate(steps, start=1):
        metrics = by_step.get(i, [])
        total = sum(m for m in metrics if isinstance(m, (int, float)))
        conv = f" ({total / prev_metric:.1%} of step {i - 1})" if prev_metric else ""
        print(f"  Step {i} - {s['label']}: {total:,.0f}{conv}")
        prev_metric = total or prev_metric


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--search", help="List real flows whose name contains this text, with their flow_id.")
    parser.add_argument("--flow-id", help="Run the funnel approximation for this real flow_id.")
    parser.add_argument("--days", type=int, default=30, help="Funnel lookback window in days (default 30).")
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
        approximate_flow(args.flow_id, args.days)
        return

    parser.print_help()


if __name__ == "__main__":
    main()

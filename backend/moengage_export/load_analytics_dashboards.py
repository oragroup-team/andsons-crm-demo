"""One-off/periodic loader: the REAL, LIVE MoEngage Analytics Dashboards
API (moengage_client.py's list_chart_catalog/get_chart_data - the same
client this app's own Slack analytics bot already uses for auth/retries)
-> a real BigQuery table in crm-mail-automation-dev.crm_analytics_views,
alongside the Campaigns Search API loader (load_campaigns_search_api.py)
and the Flows-report-CSV-loaded moengage_* tables (load_moengage_csvs.py).

Why this exists: BI has been pulling this exact data by hand - opening
each of the real dashboards/charts in the MoEngage UI one at a time and
reading numbers off the screen ("BI is using a very manual way of getting
CRM data atm - manually clicking one by one"). This automates that
entirely: one run pulls EVERY real chart's data via the same real API,
flattens it into one clean, queryable table, and loads it - no manual
export, no per-chart clicking, no MoEngage login needed at all to get the
numbers.

REAL SHAPE, confirmed live (not guessed) across chart samples spanning a
dozen+ different real dashboards - daily stats, funnels, trends, delivery
performance - before writing this: every chart's real `data` is a flat
list of rows shaped {step, metric, granularity, splitby, grouped_by,
split_by_weight, tseq, cseq}. step/metric's real TYPES vary by chart
(step can be an int or a letter like "A"; metric int or float) but the
KEYS are consistent across every real chart type sampled. splitby/
grouped_by are themselves real lists of {"key", "value"} pairs - a
genuine segmentation breakdown (e.g. which real campaign/flow a row's
number belongs to, confirmed live on "Automation Performance - Email
Delivered": splitby held the real campaign name). Every real sample
pulled had at most one such dimension, so the first is flattened into its
own queryable columns (splitby_key/splitby_value) for a normal BI GROUP
BY/WHERE - the full raw list is also kept as JSON so nothing is lost if a
chart is ever found with more than one.

A chart that fails to fetch (deleted, permission change - confirmed live,
one real chart 404'd mid-run against a stale chart_id) contributes zero
rows but is reported by name, never silently dropped from the run.

STATIC SNAPSHOT, not live: same reasoning as the other moengage_export/
loaders - re-run this periodically (by hand, or on a cron) to refresh.
WRITE_TRUNCATE means each run fully replaces the previous snapshot, not
appends to it.

HOW TO RUN (from the backend/ directory, with this repo's .env already
filled in with real MOENGAGE_WORKSPACE_ID/MOENGAGE_DATA_API_KEY/
MOENGAGE_DC, and `gcloud auth application-default login` done once as a
real BigQuery-writer identity for this project - not the deployed app's
own read-only service account, see the credentials note below):

    cd backend
    python3 moengage_export/load_analytics_dashboards.py

Takes ~1 minute (fetching ~138 real charts) the first time; safe to
re-run anytime to refresh the table with the latest numbers.
"""
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Real, live-caught bug this works around (same fix as
# load_campaigns_search_api.py): load_dotenv() above also loads
# GOOGLE_APPLICATION_CREDENTIALS (the deployed app's own BigQuery service
# account, which only has READER access to this project's dataset - a
# 403 "bigquery.tables.create denied" the moment this var is in play).
# This script needs to CREATE/WRITE a table, which needs the operator's
# own real gcloud identity instead - unset it before the BigQuery client
# resolves its credentials so it falls through to that identity.
os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402

from google.cloud import bigquery  # noqa: E402 - must come after the env pop above

sys.path.insert(0, ".")
import moengage_client as mc  # noqa: E402

PROJECT = "crm-mail-automation-dev"
DATASET = "crm_analytics_views"
TABLE_NAME = "moengage_analytics_dashboards"
_MAX_WORKERS = 6  # same concurrency moengage_client.py itself settled on -
# see that module's own comment on why higher actually hurts at this
# workspace's scale (more read timeouts, not fewer).


def _flatten(dashboard_name: str, chart_name: str, rows: list) -> list:
    """One real chart's data rows -> BigQuery-ready rows, each labelled
    with which real dashboard/chart they came from. dashboard_name/
    chart_name are passed in separately (from the catalog, not parsed
    back out of a joined "dashboard - chart" label) - a real, live-caught
    bug in an earlier version of this script: several real dashboard names
    themselves contain " - " (e.g. "Consult - Treatment Purchase"), which
    silently mis-split when reconstructed from a single joined string."""
    out = []
    for row in rows:
        splitby = row.get("splitby") or []
        first_split = splitby[0] if splitby else {}
        weight = row.get("split_by_weight")
        out.append({
            "dashboard_name": dashboard_name,
            "chart_name": chart_name,
            # step is a real int on some chart types, a letter ("A") on
            # others - normalized to a string so one column holds both
            # rather than a schema-autodetect mismatch across chart types.
            "step": str(row.get("step")) if row.get("step") is not None else None,
            "metric": row.get("metric"),
            "granularity": row.get("granularity"),
            "splitby_key": first_split.get("key"),
            "splitby_value": first_split.get("value"),
            "splitby_json": json.dumps(splitby) if splitby else None,
            "grouped_by_json": json.dumps(row.get("grouped_by")) if row.get("grouped_by") else None,
            "split_by_weight": weight if isinstance(weight, (int, float)) else None,
            "tseq": row.get("tseq"),
            "cseq": row.get("cseq"),
        })
    return out


def _fetch_one(ref: dict) -> dict:
    """Fetches one real chart's data, keeping dashboard_name/chart_name as
    the catalog's own separate fields throughout (see _flatten's docstring
    for why never re-deriving them from a joined string)."""
    try:
        data = mc.get_chart_data(ref["dashboard_id"], ref["chart_id"])
        return {"ref": ref, "rows": data.get("data") or [], "error": None}
    except Exception as exc:  # noqa: BLE001 - reported per-chart, not raised
        return {"ref": ref, "rows": [], "error": str(exc)}


def main():
    print("Listing every real dashboard/chart (~7s)...")
    refs = mc.list_chart_catalog(force_refresh=True)
    print(f"Found {len(refs)} real charts across every real workspace-level dashboard - fetching each one's data...")

    results = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = [pool.submit(_fetch_one, ref) for ref in refs]
        for future in as_completed(futures):
            results.append(future.result())

    failed = [r for r in results if r["error"]]
    if failed:
        print(f"{len(failed)} chart(s) failed and were skipped (reported here, not silently dropped):")
        for r in failed:
            print(f"  - {r['ref']['dashboard_name']} - {r['ref']['chart_name']}: {r['error']}")

    rows = []
    for r in results:
        if not r["error"]:
            rows.extend(_flatten(r["ref"]["dashboard_name"], r["ref"]["chart_name"], r["rows"]))
    print(f"Fetched {len(results) - len(failed)} real charts, {len(rows)} total data rows.")

    client = bigquery.Client(project=PROJECT)
    table_id = f"{PROJECT}.{DATASET}.{TABLE_NAME}"
    job_config = bigquery.LoadJobConfig(
        autodetect=True,
        write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    # JSONL, not CSV - same reasoning as load_moengage_csvs.py: some real
    # splitby_value strings contain characters that are safer round-tripped
    # through proper JSON string escaping than a hand-rolled CSV writer.
    tmp_path = f"/tmp/{TABLE_NAME}.jsonl"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    with open(tmp_path, "rb") as fh:
        job = client.load_table_from_file(fh, table_id, job_config=job_config)
    job.result()
    table = client.get_table(table_id)
    print(f"Loaded {table_id}: {table.num_rows} rows, {len(table.schema)} columns")


if __name__ == "__main__":
    main()

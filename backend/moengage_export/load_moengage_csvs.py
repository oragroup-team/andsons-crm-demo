"""One-off loader: real MoEngage Flows/Campaigns CSV export (the user's
'Flows_-_Schema_20260820' folder, exported directly from MoEngage's own
Flows report UI) -> real BigQuery tables in
crm-mail-automation-dev.crm_analytics_views, alongside the existing
flow_orders view. Sanitizes headers (BigQuery column names can't have
spaces/parens/%/etc.) and drops the stray leading index column each CSV
export has. Column-name collisions after sanitization are disambiguated
with a numeric suffix so no data is silently dropped/overwritten.

Why this exists: the chart-based Analytics Dashboards API (moengage_client.
get_all_chart_snapshots/list_chart_catalog) cannot answer several real
business questions at all - it has no per-campaign revenue, control-group,
or list-health (unsubscribe/complaint/bounce) figures, only whatever a
human happened to build a chart for. This real Flows/Campaigns export has
exact per-campaign numbers for all of that. See BIGQUERY_SCHEMA_NOTES in
agents/analytics_agent.py for the full documented schema and how the SQL
agent is told to use these tables.

STATIC SNAPSHOT, not live: this is a point-in-time export (2026-08-20).
To refresh, re-export the same 4 real reports from MoEngage's Flows UI
into Flows_-_Schema_20260820/ (same folder/file naming), then re-run this
script (SRC_DIR below may need updating to a new dated folder) -
WRITE_TRUNCATE means each run fully replaces the previous snapshot, not
appends to it.
"""
import csv
import os
import re
import sys

import pandas as pd
from google.cloud import bigquery

SRC_DIR = "/Users/HariKrishnaD/Downloads/ORA/CRM_Demo_Prototype/Flows_-_Schema_20260820"
PROJECT = "crm-mail-automation-dev"
DATASET = "crm_analytics_views"

FILES = {
    "moengage_flows_summary": os.path.join(SRC_DIR, "Flows_-_Schema_FLOWS_20260820.csv"),
    "moengage_campaigns_email": os.path.join(SRC_DIR, "flows", "Flows_-_Schema_flows_EMAIL_20260820.csv"),
    "moengage_campaigns_whatsapp": os.path.join(SRC_DIR, "flows", "Flows_-_Schema_flows_WHATSAPP_20260820.csv"),
    "moengage_campaigns_push": os.path.join(SRC_DIR, "flows", "Flows_-_Schema_flows_PUSH_20260820.csv"),
}


def sanitize(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = "col"
    if name[0].isdigit():
        name = "c_" + name
    return name


def dedupe(names: list) -> list:
    seen = {}
    out = []
    for n in names:
        if n not in seen:
            seen[n] = 0
            out.append(n)
        else:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
    return out


client = bigquery.Client(project=PROJECT)

for table_name, path in FILES.items():
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    # Drop the stray unnamed leading index column every export has.
    if df.columns[0] == "" or df.columns[0].startswith("Unnamed"):
        df = df.drop(columns=[df.columns[0]])

    sanitized = dedupe([sanitize(c) for c in df.columns])
    df.columns = sanitized

    table_id = f"{PROJECT}.{DATASET}.{table_name}"
    job_config = bigquery.LoadJobConfig(
        autodetect=True,
        write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    # JSONL, not CSV: the real WhatsApp/email Body/Template text fields
    # contain embedded newlines and quote characters that broke BigQuery's
    # CSV loader outright ("Missing close quote character") even though
    # pandas' own CSV writer quoted them correctly - proper JSON string
    # escaping doesn't have this fragility. Type-autodetection still runs
    # on the real string values either way.
    tmp_path = f"/tmp/{table_name}.jsonl"
    df.to_json(tmp_path, orient="records", lines=True, force_ascii=False)
    with open(tmp_path, "rb") as fh:
        job = client.load_table_from_file(fh, table_id, job_config=job_config)
    job.result()
    table = client.get_table(table_id)
    print(f"Loaded {table_id}: {table.num_rows} rows, {len(table.schema)} columns")

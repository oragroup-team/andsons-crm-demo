"""One-off/periodic loader: the REAL, LIVE MoEngage Campaigns Search API
(core-services/v1/campaigns/search, moengage_client.search_campaigns()) ->
a real BigQuery table in crm-mail-automation-dev.crm_analytics_views,
alongside flow_orders and the Flows-report-CSV-loaded moengage_* tables.

Why this exists, and how it's different from the other MoEngage tables:
confirmed live, this API returns EVERY real campaign in the account (895,
not the ~104 flows or ~821 flow-touchpoint rows the Flows report CSV
export covers) - 885 of them are ONE_TIME sends (manual blasts), a
population the Flows CSV structurally excludes entirely (it only ever
covered automated Flow-triggered touchpoints). This is the ONLY real
source in this whole system for: which campaigns actually have a control
group turned on right now (confirmed live: 6 real campaigns do, all
"Rampup_Day_N_BoostErection" - the CSV-derived tables show zero, because
none of those 6 are Flow touchpoints), the real "upgrade"/"winback"/
"replenishment" TAGS MoEngage itself assigns (not inferred from a flow
name), and the real UTM values MoEngage assigns per campaign (the actual
bridge to BigQuery's own orders_utm_source/medium/campaign columns, not a
guessed mapping).

It does NOT have performance numbers (sent/delivered/opens/clicks/revenue)
at all - confirmed live, those fields don't exist in a real response. Use
moengage_campaigns_email/whatsapp/push for performance; use this table for
real config/targeting/control-group/tag/UTM truth.

STATIC SNAPSHOT, not live: fetching all 895 real campaigns takes ~2.5
minutes (confirmed live, MoEngage's own real per-page limit is 15), far
too slow for a live per-question fetch - same reasoning as the CSV loader.
Re-run this script periodically to refresh (WRITE_TRUNCATE - each run
fully replaces the previous snapshot).

Drops the real HTML email body (campaign_content.content.email.html_content)
before loading - it can be tens of KB per campaign, has no analytical
value as a BigQuery column, and isn't needed for any of the real questions
this table exists to answer (subject line is kept; full body isn't).
"""
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Real, live-caught bug this works around: load_dotenv() above also loads
# GOOGLE_APPLICATION_CREDENTIALS (the deployed app's own BigQuery service
# account, which only has READER access to this project's dataset - real,
# confirmed live: a 403 "bigquery.tables.create denied" the moment this
# var was in play). This script needs to CREATE/WRITE a table here, which
# needs the operator's own real gcloud identity (the same one `bq mk`/
# `bq query` already used successfully for flow_orders and the other
# moengage_* tables) - unset it before the BigQuery client resolves its
# credentials so it falls through to that identity instead.
os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

from google.cloud import bigquery  # noqa: E402 - must come after the env pop above

sys.path.insert(0, ".")
import moengage_client as mc  # noqa: E402

PROJECT = "crm-mail-automation-dev"
DATASET = "crm_analytics_views"
TABLE_NAME = "moengage_campaigns_live"


def _flatten(campaign: dict) -> dict:
    basic = campaign.get("basic_details") or {}
    cg = campaign.get("control_group_details") or {}
    utm = campaign.get("utm_params") or {}
    goals = (campaign.get("conversion_goal_details") or {}).get("goals") or []
    seg = campaign.get("segmentation_details") or {}
    content = campaign.get("campaign_content") or {}
    email_content = (content.get("content") or {}).get("email") or {}
    connector = campaign.get("connector") or {}

    return {
        "campaign_id": campaign.get("campaign_id"),
        "name": basic.get("name"),
        "channel": campaign.get("channel"),
        "campaign_delivery_type": campaign.get("campaign_delivery_type"),
        "content_type": basic.get("content_type"),
        "tags": ",".join(basic.get("tags") or []) or None,
        "status": campaign.get("status"),
        "created_by": campaign.get("created_by"),
        "created_at": campaign.get("created_at"),
        "sent_time": campaign.get("sent_time"),
        "is_global_control_group_enabled": cg.get("is_global_control_group_enabled"),
        "is_campaign_control_group_enabled": cg.get("is_campaign_control_group_enabled"),
        "campaign_control_group_percentage": cg.get("campaign_control_group_percentage"),
        "utm_source": utm.get("utm_source"),
        "utm_medium": utm.get("utm_medium"),
        "utm_campaign": utm.get("utm_campaign"),
        "connector_type": connector.get("connector_type"),
        "connector_name": connector.get("connector_name"),
        "conversion_goal_names": ",".join(g.get("name", "") for g in goals if g.get("name")) or None,
        "conversion_goal_count": len(goals),
        "is_all_user_campaign": seg.get("is_all_user_campaign"),
        "email_subject": email_content.get("subject"),
        # Raw filter trees kept as JSON text, not broken into columns - the
        # real nested filter logic (action/attribute conditions, AND/OR
        # trees) doesn't map cleanly onto flat SQL columns, and no question
        # this table was built for needs to query INSIDE a filter
        # condition - this is here so a genuinely deep "what does this
        # campaign actually target" question can still be answered by
        # reading the real JSON, not so it can be filtered/grouped on.
        "included_filters_json": json.dumps(seg.get("included_filters")) if seg.get("included_filters") else None,
        "excluded_filters_json": json.dumps(seg.get("excluded_filters")) if seg.get("excluded_filters") else None,
    }


def main():
    print("Fetching every real campaign from the Campaigns Search API (~2.5 min, 895 campaigns, 15/page)...")
    campaigns = mc.search_campaigns(force_refresh=True)
    print(f"Fetched {len(campaigns)} real campaigns.")

    rows = [_flatten(c) for c in campaigns]

    client = bigquery.Client(project=PROJECT)
    table_id = f"{PROJECT}.{DATASET}.{TABLE_NAME}"
    job_config = bigquery.LoadJobConfig(
        autodetect=True,
        write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
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

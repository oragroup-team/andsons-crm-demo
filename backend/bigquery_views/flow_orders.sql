-- crm-mail-automation-dev.crm_analytics_views.flow_orders
--
-- Real fix, not a prompt patch: the Analytics agent (agents/analytics_agent.py)
-- used to reconstruct "is this order attributed to a CRM flow" and "which
-- flow" filter logic from scratch on every question, guided only by prose in
-- the system prompt. That produced two real, verified incidents:
--   1. A "how did flows perform" question answered with the WHOLE hair-loss
--      category's revenue (SGD 258,194) because the ATM_ automation-prefix
--      filter got silently dropped - the real flow-attributed figure was
--      SGD 1,005.51 for that month, a ~257x overstatement.
--   2. A named-flow question ("winback revenue") used a guessed
--      LIKE '%winback%' pattern that only matched some of the ~200 real,
--      differently-named campaign-tag variants for that flow, undercounting
--      it by 2-4x depending on which sample of variants the guess happened
--      to include.
-- Both failure classes share one root cause: the correct filter logic was
-- being re-derived by the model, from prose, on every single question,
-- instead of being defined once, correctly, and verified against real data.
-- This view is that one correct definition - see BIGQUERY_SCHEMA_NOTES in
-- agents/analytics_agent.py for how it's used.
--
-- Lives in crm-mail-automation-dev (a project this app already owns) rather
-- than in the shared ora-bigquery.ora_bigquery_pipeline warehouse itself -
-- deliberately not creating persistent objects in a dataset other brands/
-- teams share. Must be in the SAME region as the source dataset
-- (asia-southeast1) - BigQuery views can't reference a different-region
-- dataset directly.
--
-- flow_family's keyword list covers the CRM lifecycle flows that have
-- actually caused incidents or appear in this system's own flow catalog
-- (flows.py) - orders_utm_campaign is a much broader marketing-attribution
-- field (paid ads, affiliate codes, sale promos all live there too), so an
-- order not matching any of these genuinely isn't one of these named flows,
-- not a gap in the regex. Extend this CASE statement (and re-run this file)
-- if a new recurring flow name starts showing up as NULL that shouldn't.
--
-- To apply changes:
--   bq query --project_id=crm-mail-automation-dev --use_legacy_sql=false < backend/bigquery_views/flow_orders.sql

CREATE OR REPLACE VIEW `crm-mail-automation-dev.crm_analytics_views.flow_orders` AS
SELECT
  *,
  STARTS_WITH(UPPER(IFNULL(orders_utm_campaign, '')), 'ATM_') AS is_flow_attributed,
  (
    LOWER(IFNULL(status, '')) LIKE '%refund%'
    OR LOWER(IFNULL(status, '')) LIKE '%cancelled%'
    OR LOWER(IFNULL(status, '')) LIKE '%expired%'
  ) AS is_excluded_status,
  CASE
    WHEN REGEXP_CONTAINS(LOWER(IFNULL(orders_utm_campaign, '')), r'winback') THEN 'winback'
    WHEN REGEXP_CONTAINS(LOWER(IFNULL(orders_utm_campaign, '')), r'abandon|cart.?recover|[-_]ac[-_]|ac_dc|ac_otc') THEN 'abandoned_cart'
    WHEN REGEXP_CONTAINS(LOWER(IFNULL(orders_utm_campaign, '')), r'welcome|onboard') THEN 'welcome_onboarding'
    WHEN REGEXP_CONTAINS(LOWER(IFNULL(orders_utm_campaign, '')), r'tp_email|treatment.?plan') THEN 'treatment_plan_email'
    WHEN REGEXP_CONTAINS(LOWER(IFNULL(orders_utm_campaign, '')), r'order_approved|order.?confirm') THEN 'order_confirmation'
    WHEN REGEXP_CONTAINS(LOWER(IFNULL(orders_utm_campaign, '')), r'no-?show') THEN 'no_show_consultation'
    WHEN REGEXP_CONTAINS(LOWER(IFNULL(orders_utm_campaign, '')), r'renewal') THEN 'prescription_renewal'
    WHEN REGEXP_CONTAINS(LOWER(IFNULL(orders_utm_campaign, '')), r'cross.?sell') THEN 'cross_sell'
    ELSE NULL
  END AS flow_family
FROM `ora-bigquery.ora_bigquery_pipeline.updated_sales_data`;

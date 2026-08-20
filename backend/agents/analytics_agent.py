"""Analytics chat agent - LangChain SQL agent over the live ORA BigQuery
warehouse, plus real MoEngage campaign/engagement data when it's actually
relevant to the question (moengage_summary.py, shared with the email bot's
insight pipeline - same scan-then-summarize approach, same guardrails).

The agent must NEVER state a number in its final answer that didn't come
from an actual query result (or, for MoEngage, an actual chart value).
Enforced with a system-prompt instruction PLUS a post-hoc check: every
number in the final answer is confirmed to appear somewhere in this run's
tool (query) results, uploaded file, or MoEngage context; if any number
can't be traced, the answer is replaced with an explicit "I couldn't
verify that figure".

The SQL query actually executed is captured and returned alongside the
answer so it can be shown in the demo. `moengage_used` in the return value
reports whether MoEngage context was actually pulled in for this answer
(most questions won't need it - it's only fetched/folded in when relevant).
"""
import functools
import logging
import os
import re
from datetime import date
from typing import Literal, Optional

from langchain_community.agent_toolkits.sql.base import create_sql_agent
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from moengage_summary import gather_moengage_context
from text_sanitize import sanitize_text

from .llm_provider import get_llm

logger = logging.getLogger("analytics_agent")

BIGQUERY_SCHEMA_NOTES = """SCHEMA NOTES (this is the real ORA group data warehouse - it holds every ORA \
brand and country together, so filtering correctly is essential, not optional):
- ALWAYS filter Brand = 'AndSons' AND Country = 'Singapore' by default in every query on \
dotcom_plus_marketplace, updated_sales_data, and marketing_spend_data, unless the question explicitly \
asks about another brand (Ova, Modern Molecules, WithJuno) or another country (andSons also has rows for \
Malaysia and Philippines) - forgetting this filter silently mixes in other brands'/countries' real data.
- dotcom_plus_marketplace is the primary order-line sales fact table (Country, Brand, Channel, order_id, \
status, Revenue, Final_Revenue, New_COGS, Order_Type, Revenue_Type, Prescription_Type, Applicable_Discount, \
Applicable_Cashback, Delivery_Fee, quantity, product_category, created_at). Use this table for standard \
revenue/order questions.
- CATEGORY FIELD - a real, verified data-quality trap: updated_sales_data (and its flow_orders view) has \
BOTH product_category (values like "Hair Loss", "Erectile Dysfunction", "Weight Loss") AND \
new_product_category (short codes: "HL", "ED", "PE", "Weight_Loss", "Consult", "Well_Being", "SC", \
"Sexual Health", "Weight_Loss_Program", "Supplements") - these are NOT interchangeable aliases for the \
same thing. new_product_category is the more complete, corrected field - verified directly: every single \
row product_category correctly identifies, new_product_category also identifies, PLUS real additional rows \
product_category misses entirely (confirmed live: product_category = 'Hair Loss' alone undercounted a real \
flow-revenue answer by about 5% versus the complete figure, and this same gap - product_category missing \
rows new_product_category catches - holds across every category checked, not just hair loss). ALWAYS \
filter on new_product_category for any category-scoped question (hair loss, ED, weight loss, etc.) - \
never product_category alone, and don't assume they'd return the same rows just because they sound like \
the same categorization.
- status values are channel-prefixed, e.g. "[Dotcom] DELIVERED", "[Dotcom] PACKED_DISPATCHED", \
"[Dotcom] PAID_CONSULTATION_ONLY", "[Dotcom] REFUND", "[Dotcom] PAYMENT_EXPIRED", "[Marketplace] \
Completed", "[Marketplace] Confirmed", "[Marketplace] Cancelled", "[Marketplace] Delivered" - match with \
LIKE '%DELIVERED%' / '%Completed%' style patterns rather than assuming the exact prefix. ALWAYS exclude \
REFUND/CANCELLED/EXPIRED-style statuses by default on EVERY revenue or order-count question on this table \
or updated_sales_data - not only ones phrased like "how much did we sell", but also comparisons, shares, \
rates, and trends - so that two numbers being compared or combined are always measuring the same \
population. Only include them if the question explicitly asks about refunds/cancellations, or asks for a \
gross/all-orders figure specifically.
- Channel spans Dotcom, Shopee, Lazada, Zalora, and TikTok - andSons sells on real marketplaces, not just \
its own site. Default to no channel filter (all channels) unless asked about one specifically.
- Order_Type is "Products" or "Consult Only" (free doctor-led consult, no product, revenue is 0).
- Revenue_Type has many real values (e.g. "One-off", "Repeat One-off", "New One-off", "Repeat 3 Month \
Sub", "Repeat 6 Month Sub", "New Customer New 3 Month Sub") - use LIKE patterns for "subscription" vs \
"one-off" style groupings rather than an exact match on one string.
- Prescription_Type is "Prescription" or "Non-Prescription" - never name the specific prescription \
medicine even if the data contains it; refer to it only as "the doctor-prescribed plan".
- Revenue is gross; Final_Revenue is net of discounts/cashback (use Final_Revenue for "how much revenue" \
unless gross is asked for). New_COGS is cost of goods sold. Applicable_Discount/Applicable_Cashback are \
per-order amounts, not rates.
- updated_sales_data is a broader, richer table (customer email/phone, utm_source/utm_campaign, \
signup_timestamp, cohort/attribution fields) - use it only when a question needs customer-level \
attribution or cohort data that dotcom_plus_marketplace doesn't have. NEVER sum revenue across both \
tables together in the same total - that double-counts the same orders. updated_sales_data.email and \
.phone are real customer PII - never state one in an answer, aggregate only.
- EMAIL / CRM IMPACT: updated_sales_data.orders_utm_medium is the real (order-level) proxy for \
email-driven orders - values include "email", "EMAIL", and "ATM_EMAIL" (case-varies, match with LOWER() \
LIKE '%email%'). orders_utm_source also includes "MoEngage" and "Insider", which are real CRM/email \
marketing platforms - include them when a question is about CRM/lifecycle-email impact broadly. This is \
order-attribution, not send/open/click event data - phrase answers as "email-attributed orders/revenue", \
never as "opens" or "clicks" (that data doesn't exist here).
- CRM/FLOW QUESTIONS - USE THE VERIFIED VIEW, NOT RAW orders_utm_campaign MATCHING: for ANY question about \
a named CRM lifecycle flow (winback, abandoned cart, welcome, treatment-plan email, order confirmation, \
no-show consultation, prescription renewal, cross-sell) or about "flows"/"automation" as a whole, query \
`crm-mail-automation-dev.crm_analytics_views.flow_orders` (a view over the real updated_sales_data, same \
columns, same Brand/Country/Year/Month_Name/Final_Revenue/status you already know, plus three extra \
pre-verified columns - use it exactly like updated_sales_data with these three added). This view lives in \
a different project from the one you're connected to, so it will NOT appear in a table-list lookup and a \
schema-inspection tool call on it will fail (not a sign it doesn't exist, and not a sign the query itself \
will fail) - do not attempt to inspect it, and do not give up or report no data just because that lookup \
errors. Query it directly with a real SELECT using its full name and the same column names/types as \
updated_sales_data (which you already know from these schema notes) plus the three documented below - \
that SELECT will succeed even though a schema/table-list lookup on this specific table would not:
  - is_flow_attributed (BOOL): TRUE for any order attributed to an automated/orchestrated CRM flow (the \
real orders_utm_campaign "ATM_" prefix convention, already resolved for you). Use this ONLY for "all \
flows/automation as a whole" questions that don't name one specific flow.
  - flow_family (STRING or NULL): one of 'winback', 'abandoned_cart', 'welcome_onboarding', \
'treatment_plan_email', 'order_confirmation', 'no_show_consultation', 'prescription_renewal', 'cross_sell', \
or NULL if the order isn't attributed to one of these named lifecycle flows (could be a paid ad, an \
affiliate code, a sale promo, etc. - orders_utm_campaign is a much broader marketing-attribution field, not \
CRM-flow-only). This already accounts for the real sprawl of hundreds of dated/product-specific exact \
campaign-tag variants sharing a family name (e.g. "winback-ed-30jul", "20260716_PE_Winback_July Winback \
Drive_Churned Lifetime", "Cart_Recovery_Personalized", "Abandoned Cart_Static" all correctly resolve to \
their family) - filter on flow_family = 'winback' directly; never try to rebuild this with your own \
LIKE '%keyword%' guess on the raw table, real variants use inconsistent naming a guessed pattern will miss.
  CRITICAL - these two are DIFFERENT signals, do not combine them for a named-flow question: a question \
about ONE named flow ("how much did winback drive") filters on flow_family ALONE - do NOT also require \
is_flow_attributed, because plenty of real winback (and other named-flow) orders don't happen to carry the \
ATM_ prefix, so adding that condition silently drops most of the real matching orders down to a tiny sliver \
(a real, verified case of this exact mistake: combining both conditions for a winback question kept only \
1 of 88 real matching orders, understating a SGD 3,912 answer as SGD 65). is_flow_attributed is reserved \
for aggregate "all flows" questions where no single flow_family is named.
  - is_excluded_status (BOOL): TRUE for refund/cancelled/expired-style statuses (the same default exclusion \
rule already covered below) - filter WHERE NOT is_excluded_status for the normal case, or include everyone \
regardless of this flag when a question explicitly asks for a gross/all-orders figure.
This view exists because both of these were real, verified incidents caught and corrected: (1) a query \
that skipped the ATM_ automation filter answered "how did flows perform" with the whole hair-loss \
category's revenue (SGD 258,194) instead of flow-attributed revenue (the real figure, SGD 1,005.51 for \
that month) - a ~257x overstatement; (2) a guessed LIKE '%keyword%' pattern for a specific flow matched \
only some of the real campaign-tag variants, undercounting a flow's true revenue by 2-4x depending on the \
flow, because real variants don't share one consistent substring. Both failure classes are structurally \
fixed by using this view's pre-verified columns instead of re-deriving the filter logic per question.
- marketing_spend_data holds spend by Country, Brand, Channel, and month (Spends, Clicks, Impressions), \
at several Classification levels: "Category-Level" (paired with a Category like 'HL' for Hair Loss, \
'Weight_Loss', 'Supplements', 'EDPE'), "Overall-Level" (whole-account spend on that channel), and \
"Middle-Tier". NEVER sum different Classification levels together in the same total - that double-counts \
spend. Default to Category-Level rows (Category = 'HL') for "marketing spend" questions about hair loss \
specifically; ask/clarify or use Overall-Level for whole-account spend questions.
- MOENGAGE CAMPAIGN-LEVEL DATA - REAL, STRUCTURED, EXACT (prefer these tables over the chart-based \
MoEngage tool for anything they cover - an exact queried number beats an LLM's read of a chart every \
time). Source: MoEngage's own real "Flows" report, exported 2026-08-20 - a STATIC SNAPSHOT as of that \
date, NOT live-updating like the chart-based tool; say so plainly if asked how current this is. These \
live in crm-mail-automation-dev (this app's own project, not the connected ora-bigquery warehouse) - \
same real situation as flow_orders above: a schema-inspection tool call on them will fail (cross-project) \
- that is not a sign they don't exist, query them directly by full name regardless.
  - crm-mail-automation-dev.crm_analytics_views.moengage_flows_summary (263 real flows, one row each): \
Flow_Name, Flow_Status, Campaign_Channel, Global_CG_enabled (BOOL), Campaign_Control_Group_Percentage, \
Trips_Started_Total_users, Trips_Started_CG_Users, plus the Goal/attribution-window/control-group columns \
described below.
  - crm-mail-automation-dev.crm_analytics_views.moengage_campaigns_email (821 real email sends, one row \
per real campaign/touchpoint): Campaign_Name, Campaign_ID, Flows_Name (which real flow it belongs to), \
Campaign_Status, Total_Sent, Total_Delivered, Open_rate, CTR, Hard_bounce_rate, Soft_bounce_rate, \
Unsubscribe_rate, Complaints_rate - ALL real percentages (0-100 scale) - the actual real source for list-\
health questions (unsubscribes/complaints/bounces), which the chart-based tool cannot answer at all.
  - crm-mail-automation-dev.crm_analytics_views.moengage_campaigns_whatsapp (163 real sends): same shape, \
WhatsApp-specific fields too (Read_Rate, CTOR, Body/Header/Footer real template text).
  - crm-mail-automation-dev.crm_analytics_views.moengage_campaigns_push (51 real sends): same idea, \
broken out PER PLATFORM - Android_/Ios_/Web_/All_Platform_ prefixes on almost every column, since a push \
send can behave differently per OS. Use the All_Platform_* columns for a platform-unspecified question.
  - COLUMN NAMING PATTERN (all 4 tables, learn this once, don't guess per question): <Goal_1 or Goal_2> \
(a campaign can have up to 2 real conversion goals) + <Click_Through / View_Through / In_Session> \
(attribution window - Click_Through is the default/most meaningful for "did this send cause a purchase") \
+ one of: Total_Revenue, CVR, Converted_Users, Conversion_Events, Average_Order_Value, \
Control_Group_CVR, Control_Group_Uplift, Global_Control_Group_CVR, Global_Control_Group_Uplift, \
Control_Group_Conversions, Global_Control_Group_Conversions. E.g. Goal_1_Click_Through_Total_Revenue is \
Goal 1's real revenue attributed to people who clicked the send - the real source for "which campaigns \
drive revenue" and "email-driven conversion" questions (Goal_1_Click_Through_CVR). THESE ARE REAL, REAL \
MONETARY VALUES, NOT COUNTS: Goal_1_Click_Through_Total_Revenue (and its View_Through/In_Session and \
Goal_2 counterparts) genuinely holds a real SGD figure per campaign in all 3 campaign-level tables \
(email/whatsapp/push) - confirmed live and non-zero for real campaigns. Never claim these tables "contain \
no monetary values" - verify that claim against a real query before ever stating it, because it is false \
for these specific tables; SUM(Total_Revenue-shaped column) / SUM(Sent-shaped column) is real, computable \
revenue-per-send for any real Flows_Name.
  - VERIFIED REAL FACT about control groups, not something to re-derive per question - independently \
confirmed live against every real row in this data: NOT ONE of the 263 real flows or any real campaign \
in any channel has a nonzero Campaign_Control_Group_Percentage or Global_CG_enabled=TRUE. Control groups \
have never actually been turned on for any real andSons campaign. A question asking "which campaigns \
show incremental revenue vs a control group" has one true, confident answer: none currently do - no \
campaign has a control group running, so incremental/uplift attribution genuinely isn't computable for \
any of them right now. State this plainly and confidently as a verified fact, not a data gap you \
couldn't find - a query confirming zero rows with a real control group is itself the complete, correct \
answer.
  - Campaign_Name is NOT a unique key - the same display name can appear on multiple real rows (a live \
send and an earlier draft/version, or a genuine re-run); Campaign_ID is the real unique identifier - \
group/filter by Campaign_ID when counting distinct real campaigns.
  - Flows_Name links each individual send to the real flow it belongs to - use it for "lifecycle stage" \
questions by matching against real flow names (e.g. names containing "Winback" are the real winback \
lifecycle stage - query these tables and report its real revenue-per-send even if other named stages in \
the same question don't exist, same principle as reporting a partial real answer rather than none at \
all). There is NO separate "Sale", "Upgrade", or "Edu" lifecycle category anywhere in this real data - if \
asked about one of those specifically, say plainly that this exact categorization doesn't exist in the \
real MoEngage setup rather than forcing a different real flow into that label, but ALWAYS query these \
tables first to check and report what real, named stages DO exist (e.g. Winback) before concluding \
anything is missing - never conclude "not tracked" from the chart-based tool's own catalog check alone \
when these real per-campaign tables haven't been queried yet; they are the first, primary place to look \
for ANY question about campaign performance by name, product line, or channel, ALWAYS before the chart \
tool and before falling back to a BigQuery order-attribution proxy (e.g. orders_utm_medium share) for \
something these tables already report directly and exactly (conversion rate, revenue, funnel step \
rates like open/click/CVR in sequence).
  - Still use the chart-based MoEngage tool for anything these tables don't cover (day-by-day trend \
detail, funnel step-by-step breakdowns not captured as a Goal here)."""

SYSTEM_PREFIX_TEMPLATE = """You are the andSons analytics assistant. andSons is a men's health telehealth \
brand (hair loss is the flagship vertical, alongside weight loss and other supplements); all prices are \
in SGD. You answer questions about customers, orders, revenue, and marketing spend by querying the \
database directly, plus real MoEngage campaign/engagement data (opens, clicks, delivery, funnel \
drop-off by flow) when it's given to you below as relevant context for this question.

TODAY'S REAL DATE IS {today}. Use this to resolve any relative time reference ("this month", "last \
quarter", "so far this year", "recently") to real calendar dates.

YEAR RESOLUTION - a real, serious mistake this caused before, fix it properly every time: when a question \
names a month WITHOUT a year (e.g. "how did July perform", "revenue in March"), do NOT guess or default \
to any particular year - the real data spans multiple years (2021 through the current year), and picking \
the wrong one silently answers about an empty or irrelevant period. Either (a) run a quick query first to \
see which year(s) actually have rows for that month for the population you're about to filter to, and use \
the most recent one with real data, or (b) if the question is naturally about "the current"/"this" month \
by context, use today's real year above. Never hardcode a year (e.g. in a date-range filter) that you \
haven't actually confirmed has data - an empty result you can't explain to the user is worse than one \
extra exploratory query. Prefer the table's own Year/Month_Name columns (exact values, no date-arithmetic \
ambiguity) over constructing a created_at BETWEEN/date-range filter when both are available for the same \
table - simpler and less error-prone.

{schema_notes}

CRITICAL RULE: Every number you state in your final answer MUST come from an actual query result you \
ran in this conversation. Never estimate, round beyond what you computed, or state a number from prior \
knowledge or assumption. If you cannot compute an exact number with the available tables, say so \
explicitly instead of guessing.

PERCENTAGES, RATES, AND COMPARISONS: if your answer is going to state a percentage, rate, ratio, \
average, or a comparison between two totals, compute that number DIRECTLY in the SQL query itself \
(e.g. SELECT ROUND(100.0 * SUM(CASE WHEN Order_Type = 'Consult Only' THEN 1 ELSE 0 END) / COUNT(*), 2) AS \
consult_rate ...) rather than fetching the raw counts and doing the division/subtraction yourself when \
writing the answer. A query that returns only raw component counts is not enough on its own - add the \
computed rate/percentage/difference as its own column in the same query or a follow-up query, so the \
exact number you state is the exact number SQL returned.

SANITY-CHECK YOUR OWN RESULT BEFORE ANSWERING - think like an analyst who'd be embarrassed to be wrong, \
not like someone reporting whatever a query happened to return: before you finalize a headline number, \
ask yourself whether it's actually plausible for what was asked. Concrete real example: a query answering \
"how did our flows perform" returned a number that was actually the ENTIRE product category's revenue \
because a real filter got dropped - a human analyst who knew the business would have sensed something \
was off (a specific automation flow's revenue being close to 100% of a whole category's revenue is an \
immediate red flag, not a headline to report proudly) and gone back to check the filter before answering. \
Concrete checks worth a second before you answer: does a "flow-specific" or "campaign-specific" number \
look suspiciously close to a much broader total you could compare it against (category, brand, company-\
wide) - if so, re-verify the specific filter actually narrowed the population; does a rate/percentage land \
outside a sane range (e.g. a share over 100%, an open rate above 100%, a negative count); does a total for \
a short/narrow window look implausibly large relative to a longer/broader one you also computed. If \
something looks off, re-run the check with a tighter or corrected filter before answering - don't report a \
number that doesn't pass your own smell test just because SQL executed without an error.

READ-ONLY, NO EXCEPTIONS: you may only ever run SELECT queries. Never write, generate, or attempt an \
INSERT, UPDATE, DELETE, UPSERT, MERGE, DROP, ALTER, TRUNCATE, CREATE, or REPLACE statement, even if the \
question asks for it directly or implies fixing/changing a record - if a question asks you to change \
data, refuse and tell the user you can only read and report on data, and ask them to rephrase the \
question as a lookup instead.

CUSTOMER PRIVACY: never state an individual customer's email address, or any other single customer's \
personal contact details, in your final answer - not even if a query result contains one. Answer only \
with aggregates (counts, sums, rates, averages) and never a named individual's personal data. If a \
question specifically asks for one customer's personal details (e.g. "what is customer X's email"), \
refuse and explain you can only provide aggregated analytics, not individual customer records.

Always run a SQL query to get the real answer before responding - do not answer from memory.

Answer style: Lead with the headline number in the first sentence, stated plainly - don't bury it \
behind a description of how the query was built. Never narrate your own SQL or filter logic back to \
the reader ("counting each distinct order ID", "met those status criteria", "orders that satisfy this \
condition") - that describes the query, not the business reality; say what the number actually MEANS \
instead (e.g. "132,906 completed orders" rather than "132,906 orders that met the status criteria").
Then add ONE genuinely useful piece of context that makes the number meaningful on its own, computed \
with a real follow-up query rather than guessed: a natural breakdown (by channel, by prescription \
type, by month), a rate or share (e.g. what fraction of orders that is), or a comparison to a related \
total (gross vs net, this period vs another). Pick whichever breakdown is most relevant to the \
question rather than a generic one. Skip this second query only for questions where no such breakdown \
is meaningful.

NEVER mention the database itself - no "row(s)", "table(s)", "column(s)", "record(s)", "dataset", \
"database", "query", "SQL", "filter(ed)", "data" as a stand-in for "orders"/"customers"/"spend", or any \
internal schema/column name or its literal stored value (e.g. never say "Category-Level", \
"Classification", "Order_Type", "Brand = 'AndSons'" - translate every one of these into the plain \
business term instead: "Category-Level" + Category "HL" becomes "hair-loss-specific marketing spend", \
not a description of which rows matched). The same rule applies to any MoEngage context you're given: \
never say "chart", "dashboard", or a raw MoEngage metric/field label - translate it into the plain \
business term (e.g. a chart tracking step-1-to-step-2 dropoff on a winback flow becomes "winback \
emails that get a response", not a description of the chart). The reader should hear a business story \
told by someone who knows the numbers cold, with zero trace that the answer came from a query or a \
chart at all.
Write like a sharp analyst briefing a colleague, not like a system describing its own query: plain, \
confident, specific sentences, no hedging, no filler ("this figure reflects...", "it is worth noting \
that..."). Aim for 3 to 5 sentences for most questions, fewer for genuinely simple ones.

Formatting: Respond in plain text only, in plain English prose. Do not use markdown of any kind: no \
asterisks or underscores for bold or italics, no backticks, no headings, no bullet points or numbered \
lists, no tables. Do not use special typographic characters: no smart or curly quotes, no em or en \
dashes, no ellipsis characters, no non-breaking or unusual spaces. Use only plain straight quotes, a \
plain hyphen (-), regular periods, and normal single spaces.
"""


def _connect_bigquery() -> SQLDatabase:
    """Connect to the real ORA BigQuery warehouse. Raises clearly (caught in
    ask_analytics, surfaced as a plain error to the caller) if it isn't
    configured or isn't reachable right now - there is no mock-data
    fallback, this agent only ever answers from the live warehouse."""
    project_id = os.environ.get("BIGQUERY_PROJECT_ID")
    if not project_id:
        raise RuntimeError(
            "BIGQUERY_PROJECT_ID is not set. The analytics agent requires a live BigQuery connection."
        )

    dataset = os.environ.get("BIGQUERY_DATASET")
    uri = f"bigquery://{project_id}/{dataset}" if dataset else f"bigquery://{project_id}"

    # Real ORA warehouse datasets (e.g. ora_bigquery_pipeline) are shared
    # across multiple brands and hold dozens of staging/versioned-snapshot
    # tables alongside the real ones - restrict what the agent even sees to
    # an explicit allowlist so it can't wander into another brand's tables
    # or a stale dated snapshot. Comma-separated, optional.
    tables_env = os.environ.get("BIGQUERY_TABLES")
    include_tables = [t.strip() for t in tables_env.split(",") if t.strip()] if tables_env else None

    kwargs = {"include_tables": include_tables} if include_tables else {}
    db = SQLDatabase.from_uri(uri, **kwargs)
    db.get_usable_table_names()  # forces a real connectivity/permission check now
    logger.info("Connected to live BigQuery project %s.", project_id)
    return db


@functools.lru_cache(maxsize=1)
def _get_db_cached() -> SQLDatabase:
    """Resolve the connection once per process - BigQuery reachability
    doesn't change mid-session, so this avoids paying the connectivity-check
    cost on every question."""
    return _connect_bigquery()


NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")

# Guardrail 1: never allow (or admit to attempting) a data-altering statement.
# The read-only DB connection above makes writes physically fail, but this
# catches the attempt itself - in the user's original question and in any
# SQL the agent tried - so we can give one clear, consistent refusal instead
# of a raw database error or an inconsistent model-authored one.
_WRITE_KEYWORDS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|UPSERT|MERGE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE|ATTACH|DETACH|VACUUM)\b",
    re.IGNORECASE,
)
WRITE_BLOCKED_MESSAGE = (
    "That request would change data (insert, update, delete, or similar), and this assistant is "
    "read-only - it can only look up and report on existing data. Please rephrase your question as "
    "something that reads or summarises data instead, and I will run it again."
)

# Guardrail 2: never surface an individual customer's PII (email address) in
# the final answer, even if a query result contained one.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PII_BLOCKED_MESSAGE = (
    "I can't include an individual customer's personal contact details (like an email address) in an "
    "answer - only aggregated statistics. Please rephrase your question so it asks for a count, rate, "
    "or summary instead of a specific customer's personal information."
)


def _contains_write_operation(text: str) -> bool:
    return bool(_WRITE_KEYWORDS_RE.search(text))


def _contains_pii(text: str) -> bool:
    return bool(_EMAIL_RE.search(text))


def _extract_numbers(text: str) -> list:
    found = []
    for match in NUMBER_RE.findall(text):
        cleaned = match.replace(",", "").rstrip(".")
        if cleaned in ("", "-"):
            continue
        found.append(cleaned)
    return found


def _close(a: float, b: float, rel_tol: float = 0.02, abs_tol: float = 0.06) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b), 1))


def _is_derivable(target: float, source_numbers: list) -> bool:
    """True if `target` is a simple percentage/ratio/sum/difference of two
    numbers that genuinely came from the query results this run. Handles
    the common case where the SQL agent fetches raw component counts (e.g.
    total_sent, total_clicked) via SQL but computes the rate/percentage/
    comparison itself when writing the final answer - that number never
    appears verbatim in the tool output, but it IS a real, traceable
    derivation of real query-sourced numbers, not a fabrication."""
    for a in source_numbers:
        if _close(target, a):
            return True
    for a in source_numbers:
        for b in source_numbers:
            if a == b or b == 0:
                continue
            candidates = (100.0 * a / b, a / b, a - b, a + b)
            if any(_close(target, c) for c in candidates):
                return True
    return False


def _verify_numbers(answer: str, tool_results_text: str) -> bool:
    numbers = _extract_numbers(answer)
    if not numbers:
        return True
    haystack = tool_results_text
    source_numbers = [float(n) for n in _extract_numbers(tool_results_text)]
    for num in numbers:
        # Try exact token match first, then a loose substring match (handles
        # "39.0" vs "39.00" style formatting differences from BigQuery).
        if num in haystack:
            continue
        try:
            as_float = float(num)
        except ValueError:
            return False
        if str(as_float) in haystack or f"{as_float:.2f}" in haystack or f"{int(as_float)}" in haystack:
            continue
        if _is_derivable(as_float, source_numbers):
            continue
        return False
    return True


_CAMPAIGN_LIKE_RE = re.compile(r"orders_utm_campaign\)?\s*\)?\s*LIKE\s*'%([^%']+)%'", re.IGNORECASE)
_YEAR_EQ_RE = re.compile(r"\bYear\s*=\s*'?(\d{4})'?", re.IGNORECASE)
_MONTH_EQ_RE = re.compile(r"\bMonth_Name\s*=\s*'([A-Za-z]+)'", re.IGNORECASE)
_CREATED_AT_GE_RE = re.compile(r"created_at\s*>=\s*DATE\s*'(\d{4}-\d{2}-\d{2})'", re.IGNORECASE)
_CREATED_AT_LT_RE = re.compile(r"created_at\s*<\s*DATE\s*'(\d{4}-\d{2}-\d{2})'", re.IGNORECASE)
_REFUND_EXCLUSION_RE = re.compile(r"status\)?\s*\)?\s*NOT\s+LIKE\s*'%refund%'", re.IGNORECASE)


def _last(pattern: re.Pattern, text: str):
    """The LAST match, not the first - an exploratory step earlier in the
    trace can mention a different year/keyword than the FINAL aggregation
    that actually produced the stated answer; the final query is reliably
    the last one run, so its filters are what this check needs to mirror."""
    matches = list(pattern.finditer(text))
    return matches[-1] if matches else None


def _invoke_with_retry(chain, payload: dict, attempts: int = 5, label: str = "Analytics LLM call"):
    """Real, documented Groq failure mode, same one llm_provider.
    invoke_with_retry already fixes for the Copywriter/Sweeper (forced
    tool-calling mode rejects the call outright, 'Tool choice is required,
    but model did not call a tool', when the model tries to answer in free
    text instead of the required structured schema) - every small
    classifier call in this file (_resolve_metric_intent,
    _resolve_flow_intent, _resolve_followup_question,
    _is_moengage_exclusive) needs real per-invocation template variables
    filled, which invoke_with_retry's own hardcoded chain.invoke({})
    doesn't support, so a small local equivalent instead of forcing this
    file's calls to fit that signature (same fix already applied to
    moengage_summary.py's own calls). Real, live-caught bug this fixes:
    _is_moengage_exclusive had NO retry at all - a single transient 400 on
    that one call silently forced 'query BigQuery too' (its documented
    fail-closed default) even when MoEngage alone would have answered a
    question cleanly, leading to a blended answer whose restated numbers
    didn't trace back cleanly and got wrongly discarded as unverified.
    Raises the last exception if every attempt fails - callers already
    handle that with their own try/except."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return chain.invoke(payload)
        except Exception as exc:  # noqa: BLE001 - every attempt logged, caller decides final handling
            last_exc = exc
            logger.warning("%s failed (attempt %d/%d): %s", label, attempt + 1, attempts, exc)
    raise last_exc


class _MetricIntent(BaseModel):
    metric: Literal["revenue", "order_count", "other"] = Field(
        description="What number this question is actually asking for. 'revenue' for a dollar/SGD amount "
        "(revenue, sales, spend, value earned). 'order_count' for a plain count of orders (e.g. 'how many "
        "orders', 'how many people bought'). 'other' for anything else (average order value, unique "
        "customer count, a ratio/percentage, etc.)."
    )


def _resolve_metric_intent(question: str) -> str:
    """Independently classifies what NUMBER a question is actually asking
    for, from the question's own words - shared by both deterministic
    safety nets in this file, so neither assumes revenue by default and
    silently overwrites a correct answer with a different kind of number.
    Real, live-caught bug this fixes: a plain order-count question ('how
    many orders came from winback') got its correct integer answer
    overwritten with an unrelated revenue figure, because the only check
    either safety net ran was 'does any number in the stated answer match
    the independently-computed revenue total' - with no awareness that
    revenue might not even be what was asked for. Defaults to 'revenue' on
    any resolution failure - the long-standing prior behavior for both
    checks, not a new assumption - so a classifier hiccup degrades to the
    existing behavior rather than silently disabling every correction."""
    llm = get_llm("ANALYTICS")
    structured_llm = llm.with_structured_output(_MetricIntent)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Determine what number this question about andSons business data is actually asking for."),
        ("human", "Question: {question}"),
    ])
    chain = prompt | structured_llm
    try:
        return _invoke_with_retry(chain, {"question": question}, label="Metric-intent resolution call").metric
    except Exception as exc:  # noqa: BLE001 - fail to the prior default, never block a correction outright
        logger.warning("Metric-intent resolution failed for %r: %s - defaulting to revenue.", question, exc)
        return "revenue"


def _verify_campaign_family_total(question: str, answer: str, sql_query: str) -> Optional[str]:
    """Deterministic safety net for the campaign-name-sprawl failure mode
    (see BIGQUERY_SCHEMA_NOTES) - a prompt instruction alone can't guarantee
    a probabilistic ReAct SQL agent always builds the complete broad LIKE
    filter instead of a partial hand-enumerated one it stumbled onto while
    exploring. If the agent's own query trace used a `LIKE '%keyword%'`
    filter on orders_utm_campaign anywhere, independently re-run the
    CANONICAL, guaranteed-complete version of that same aggregation
    ourselves (never trusting the model to have done it right) and check
    the answer's stated number actually matches. Returns a corrected
    answer string if a real mismatch is found, else None (nothing to fix -
    the normal answer stands). Fails open (None) on any error, AND fails
    open (no correction) whenever the real scope (time period, refund
    inclusion) can't be confidently reconstructed from the query text -
    correcting with the wrong scope would make a right answer wrong, which
    is worse than not correcting at all."""
    match = _last(_CAMPAIGN_LIKE_RE, sql_query)
    if not match:
        return None
    keyword = match.group(1)

    try:
        db = _get_db_cached()
    except Exception:
        return None

    metric = _resolve_metric_intent(question)
    if metric == "other":
        # Same real principle as the flow_orders check's own metric guard:
        # this function can only independently verify revenue or a plain
        # order count - anything else (average order value, unique
        # customers, a ratio) must fail open rather than force-fit a
        # revenue correction onto a question this check can't actually
        # answer.
        return None

    year_match = _last(_YEAR_EQ_RE, sql_query)
    month_match = _last(_MONTH_EQ_RE, sql_query)
    created_ge = _last(_CREATED_AT_GE_RE, sql_query)
    created_lt = _last(_CREATED_AT_LT_RE, sql_query)
    scope_sql = "WHERE Brand='AndSons' AND Country='Singapore'"
    period = ""
    if year_match:
        scope_sql += f" AND Year={int(year_match.group(1))}"
        if month_match:
            scope_sql += f" AND Month_Name='{month_match.group(1)}'"
            period = f" for {month_match.group(1)} {year_match.group(1)}"
        else:
            period = f" for {year_match.group(1)}"
    elif created_ge or created_lt:
        # The model used a created_at date-range instead of Year/Month_Name
        # (against instructions, but it happens) - mirror THAT range rather
        # than silently dropping the time scope entirely, which would
        # compare an all-time total against a single-month answer and
        # "correct" a right answer into a wrong one.
        if created_ge:
            scope_sql += f" AND created_at >= DATE '{created_ge.group(1)}'"
        if created_lt:
            scope_sql += f" AND created_at < DATE '{created_lt.group(1)}'"
        period = f" for the same period as the original query"
    # else: no time scope found anywhere in the trace - assume the question
    # genuinely was an all-time total (matches the original's own scope).

    # Only exclude refunds/cancellations/expirations if the model's OWN
    # query already did - mirroring a gross-including-refunds question's
    # scope exactly, not silently narrowing it to net-of-refunds and
    # "correcting" a right inclusive answer into a wrong exclusive one.
    if _REFUND_EXCLUSION_RE.search(sql_query):
        scope_sql += (
            " AND LOWER(status) NOT LIKE '%refund%' AND LOWER(status) NOT LIKE '%cancelled%' "
            "AND LOWER(status) NOT LIKE '%expired%'"
        )

    select_expr = "COUNT(DISTINCT order_id) AS total" if metric == "order_count" else "ROUND(SUM(Final_Revenue),2) AS total"
    try:
        canonical_result = db.run(
            f"SELECT {select_expr} "
            "FROM updated_sales_data " + scope_sql +
            f" AND LOWER(orders_utm_campaign) LIKE '%{keyword.lower()}%'"
        )
        canonical_total = float(re.search(r"[-\d.]+", str(canonical_result)).group())
    except Exception:
        logger.exception("Campaign-family cross-check query failed for keyword %r (metric=%s) - skipping correction.", keyword, metric)
        return None

    stated_numbers = [float(n) for n in _extract_numbers(answer)]
    if any(_close(canonical_total, n) for n in stated_numbers):
        return None  # the model's own answer already matches the complete total - nothing to fix

    logger.warning(
        "Campaign-family mismatch for keyword %r (metric=%s): stated answer had %s, complete broad-match "
        "total is %s - correcting.",
        keyword, metric, stated_numbers, canonical_total,
    )
    if metric == "order_count":
        return (
            f"{int(round(canonical_total)):,} orders (verified against every real matching '{keyword}' "
            f"campaign variant, not a partial sample){period}."
        )
    return (
        f"SGD {canonical_total:,.2f} (verified against every real matching '{keyword}' campaign variant, "
        f"not a partial sample){period}."
    )


_FLOW_FAMILIES = (
    "winback", "abandoned_cart", "welcome_onboarding", "treatment_plan_email",
    "order_confirmation", "no_show_consultation", "prescription_renewal", "cross_sell",
)
_CATEGORY_CODES = ("HL", "ED", "PE", "Weight_Loss", "Consult", "Well_Being", "SC", "Sexual Health", "Weight_Loss_Program", "Supplements")
_CATEGORY_ALIASES = {
    "hair loss": "HL", "hairloss": "HL", "hl": "HL",
    "erectile dysfunction": "ED", "ed": "ED",
    "premature ejaculation": "PE", "pe": "PE",
    "weight loss": "Weight_Loss", "weight_loss": "Weight_Loss",
    "consultation": "Consult", "consult": "Consult",
    "well being": "Well_Being", "well_being": "Well_Being", "wellbeing": "Well_Being",
    "skincare": "SC", "sc": "SC",
    "sexual health": "Sexual Health",
    "weight loss program": "Weight_Loss_Program", "weight_loss_program": "Weight_Loss_Program",
    "supplements": "Supplements",
}


def _normalize_category_code(raw: Optional[str]) -> Optional[str]:
    """Never trust the classifier's raw string directly in SQL - it has
    been observed returning the human category name ('Hair Loss') instead
    of the required short code, and once returned brand/country text mixed
    into the field entirely. Only a recognized code or a known human-name
    alias is used; anything else is dropped (treated as no category
    constraint) rather than injected into a query unvalidated."""
    if not raw:
        return None
    if raw in _CATEGORY_CODES:
        return raw
    return _CATEGORY_ALIASES.get(raw.strip().lower())


def _detect_category_code_in_text(question: str) -> Optional[str]:
    """Deterministic fallback/cross-check for category detection - this is
    a closed, known set of category names, so a keyword scan is more
    reliable than trusting an LLM classifier to extract it correctly every
    single time (confirmed live: the same classifier, same question, only
    filled in the category on 1 of 3 calls). Longest alias first so
    'weight loss program' matches before the shorter 'weight loss'.

    WORD-BOUNDARY matching, never a bare substring check - a real, caught
    bug: the naive 'alias in text' version matched the short alias 'ed' (=
    erectile dysfunction) INSIDE the word 'abandonED', silently mis-scoping
    an abandoned-cart question to the wrong product category entirely. The
    short 2-3 letter codes (ed, hl, pe, sc) are exactly the ones likely to
    collide with ordinary English words, so this must never be a plain
    substring test."""
    q_lower = question.lower()
    for alias in sorted(_CATEGORY_ALIASES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(alias) + r"\b", q_lower):
            return _CATEGORY_ALIASES[alias]
    return None


def _normalize_flow_family(raw: Optional[str]) -> Optional[str]:
    """Same principle as _normalize_category_code - the classifier has been
    observed returning values outside the real enum (e.g. 'live', echoing
    a word from the question rather than an actual flow family)."""
    if not raw:
        return None
    normalized = raw.strip().lower().replace(" ", "_")
    return normalized if normalized in _FLOW_FAMILIES else None


class _FlowQuestionIntent(BaseModel):
    is_flow_question: bool = Field(
        description="True if this question is genuinely about CRM/lifecycle-flow-attributed revenue or "
        "orders (one named flow, or flows/automation as a whole) - False for a general revenue/category "
        "question with no flow angle at all."
    )
    metric: Literal["revenue", "order_count", "other"] = Field(
        default="revenue",
        description="What number the question is actually asking for. 'revenue' for a dollar/SGD amount "
        "(revenue, sales, spend, value earned). 'order_count' for a plain count of orders (e.g. 'how many "
        "orders', 'how many people bought'). 'other' for anything else this specific check can't verify "
        "(average order value, unique customer count, conversion rate, a ratio/percentage, etc.) - "
        "critical to get right: correcting a count question with a revenue number (or vice versa) would "
        "answer a completely different question than the one actually asked.",
    )
    wants_all_flows: bool = Field(
        default=False,
        description="True if the question asks about flows/automation AS A WHOLE (e.g. 'how did our "
        "flows perform', 'automation revenue this month') - not one named flow. False if a single named "
        "flow is asked about, or is_flow_question is False.",
    )
    named_flow_family: Optional[str] = Field(
        default=None,
        description=f"If ONE specific flow is named, which one: {', '.join(_FLOW_FAMILIES)}. Null if "
        "wants_all_flows is true, or this isn't a flow question.",
    )
    product_category_code: Optional[str] = Field(
        default=None,
        description=f"If the question scopes to one product category, its short code: {', '.join(_CATEGORY_CODES)} "
        "(e.g. hair loss is HL, erectile dysfunction is ED). Null if no category is named.",
    )
    month_name: Optional[str] = Field(
        default=None,
        description="The month name if the question asks about one specific month (e.g. 'July'), "
        "resolving a relative term like 'last month' against today's real date. Null if no specific "
        "month is being asked about (e.g. an all-time or 'this year' question).",
    )
    year: Optional[int] = Field(
        default=None,
        description="The year that goes with month_name (or a bare year if no month is named), resolving "
        "relative terms against today's real date. Null if no specific year/month is being asked about.",
    )


def _resolve_flow_intent(question: str) -> Optional[_FlowQuestionIntent]:
    """Independently re-derives what a flow-related question is actually
    asking - from the question's own words, never from the SQL the agent
    happened to write - so this verification can't inherit whatever
    mistake the agent's own query made. Same principle as
    _resolve_live_data_request(). Fails open (None) on any error."""
    llm = get_llm("ANALYTICS")
    structured_llm = llm.with_structured_output(_FlowQuestionIntent)
    system_text = (
        f"Today's real date is {date.today().isoformat()}. Determine what this question about andSons "
        "CRM/business data is actually asking, precisely enough to build the exact right database filter."
    )
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", "Question: {question}")])
    chain = prompt | structured_llm
    try:
        return _invoke_with_retry(chain, {"question": question}, label="Flow-intent resolution call")
    except Exception as exc:  # noqa: BLE001 - fail open, no correction attempted
        logger.warning("Flow-intent resolution failed for %r: %s", question, exc)
        return None


def _verify_flow_orders_answer(question: str, answer: str, sql_query: str) -> Optional[str]:
    """Deterministic safety net for the flow_orders view specifically - a
    prompt instruction alone couldn't reliably guarantee the agent applies
    BOTH is_flow_attributed/flow_family AND new_product_category correctly
    together on an unfamiliar cross-project view (confirmed live: the exact
    same question, same code, produced the right answer on some runs and a
    ~40x-too-large wrong one on others). Rather than trying to detect what
    the agent's own query got wrong, independently rebuild the correct
    query from the ORIGINAL QUESTION and compare - this can't inherit a
    mistake the agent's SQL made, because it never reads that SQL's filter
    logic at all. Fails open (None - no correction) whenever the question's
    intent can't be confidently resolved, rather than risk correcting with
    the wrong scope."""
    if "flow_orders" not in sql_query:
        return None
    try:
        db = _get_db_cached()
    except Exception:
        return None

    intent = _resolve_flow_intent(question)
    if intent is None or not intent.is_flow_question:
        return None
    if intent.metric == "other":
        # Real bug this guards against, caught live: this check used to
        # ALWAYS compute SUM(Final_Revenue) regardless of what the question
        # actually asked for - a plain order-count question ("how many
        # orders came from winback") got its correct integer answer
        # silently overwritten with an unrelated revenue figure, because
        # the only thing being compared was "does any number in the answer
        # match the revenue total", not "is revenue even the right metric
        # for this question". Anything this specific check doesn't know how
        # to independently compute (average order value, unique customers,
        # a ratio) must fail open, never force-fit a revenue correction
        # onto a question about something else entirely.
        return None
    named_flow_family = _normalize_flow_family(intent.named_flow_family)
    if not intent.wants_all_flows and not named_flow_family:
        return None  # ambiguous which flow - don't guess, don't correct
    category_code = _normalize_category_code(intent.product_category_code) or _detect_category_code_in_text(question)
    _VALID_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
                      "August", "September", "October", "November", "December")
    month_name = intent.month_name.strip().capitalize() if intent.month_name else None
    if month_name not in _VALID_MONTHS:
        month_name = None
    year = int(intent.year) if intent.year else None
    if month_name and not year:
        # Same real, verified trap as the main agent's own YEAR RESOLUTION
        # rule (see BIGQUERY_SCHEMA_NOTES) - never guess a year for a
        # month-only reference, including here in the classifier's own
        # output. Confirmed live: leaving year unresolved silently summed
        # a month across every year in the warehouse (2021-present)
        # instead of the one real year being asked about, corrupting the
        # very check meant to catch exactly this failure mode. Look up the
        # most recent year with real data for this scope directly.
        try:
            year_probe = ["Brand='AndSons'", "Country='Singapore'", f"Month_Name='{month_name}'"]
            if category_code:
                year_probe.append(f"new_product_category='{category_code}'")
            year_result = db.run(
                "SELECT MAX(Year) FROM `crm-mail-automation-dev.crm_analytics_views.flow_orders` WHERE "
                + " AND ".join(year_probe)
            )
            year_match = re.search(r"\d{4}", str(year_result))
            if year_match:
                year = int(year_match.group())
        except Exception:
            logger.warning("Year lookup for month-only flow_orders check failed - proceeding without a year filter.")

    where = ["Brand='AndSons'", "Country='Singapore'"]
    period = ""
    if year:
        where.append(f"Year={year}")
        period = f" for {year}"
    if month_name:
        where.append(f"Month_Name='{month_name}'")
        period = f" for {month_name} {year}" if year else f" for {month_name}"
    if category_code:
        where.append(f"new_product_category='{category_code}'")
    where.append("NOT is_excluded_status")
    if intent.wants_all_flows:
        where.append("is_flow_attributed")
    else:
        where.append(f"flow_family='{named_flow_family}'")

    # Which real aggregate to check against depends on intent.metric,
    # resolved above from the question's own words - never assume revenue
    # regardless of what was actually asked (see the metric field's
    # docstring for the real incident this fixes).
    if intent.metric == "order_count":
        select_sql = "SELECT COUNT(DISTINCT order_id) AS total FROM `crm-mail-automation-dev.crm_analytics_views.flow_orders` "
    else:
        select_sql = "SELECT ROUND(SUM(Final_Revenue),2) AS total FROM `crm-mail-automation-dev.crm_analytics_views.flow_orders` "

    try:
        canonical_result = db.run(select_sql + "WHERE " + " AND ".join(where))
        match = re.search(r"[-\d.]+", str(canonical_result))
        if not match:
            # A genuine NULL/no-rows result (str(canonical_result) has no
            # digits at all, e.g. "[(None,)]") - not an error, just nothing
            # to correct against.
            return None
        canonical_total = float(match.group())
    except Exception:
        logger.exception("flow_orders cross-check query failed for intent %r - skipping correction.", intent)
        return None

    stated_numbers = [float(n) for n in _extract_numbers(answer)]
    if any(_close(canonical_total, n) for n in stated_numbers):
        return None  # the agent's own answer already matches the independently-derived correct total

    logger.warning(
        "flow_orders mismatch for question %r (metric=%s): stated answer had %s, independently-derived "
        "correct total is %s - correcting.",
        question, intent.metric, stated_numbers, canonical_total,
    )
    scope_desc = named_flow_family if named_flow_family else "all flow-attributed"
    if intent.metric == "order_count":
        return f"{int(round(canonical_total)):,} orders ({scope_desc}, independently verified){period}."
    return f"SGD {canonical_total:,.2f} ({scope_desc} revenue, independently verified){period}."


class _ResolvedQuestion(BaseModel):
    standalone_question: str = Field(
        description="The follow-up question rewritten as a complete, standalone question that makes "
        "sense with zero prior context - fill in whatever it's implicitly referring to from the "
        "conversation. Critically: if the follow-up narrows/filters the previous question (by product, "
        "category, channel, time period, etc.), keep the SAME metric/topic as before, just add the new "
        "filter - e.g. after 'how are winback email open rates doing', a follow-up 'what about for hair "
        "loss specifically' resolves to 'how are winback email open rates doing for hair loss "
        "specifically', NOT a switch to an unrelated metric like revenue or order counts just because "
        "'hair loss' also appears in other data. If the follow-up is already a complete standalone "
        "question (doesn't reference prior context), return it unchanged."
    )


def _resolve_followup_question(question: str, conversation_history: list, llm) -> str:
    """Rewrites a follow-up ('what about for hair loss specifically') into a
    complete standalone question using conversation history - a standard
    technique for conversational Q&A, and more reliable than asking one
    single downstream prompt to juggle history-resolution, topic
    continuity, SQL tool-calling, and MoEngage-vs-BigQuery source selection
    all at once. Caught live: without this, follow-ups that narrowed a
    metric by category silently swapped to a different, unrelated metric
    instead of staying on-topic. Fails open (returns the question
    unresolved) rather than blocking the whole answer on a rewrite failure."""
    history_text = "\n\n".join(f"Q: {h['question']}\nA: {h['answer']}" for h in conversation_history)
    structured_llm = llm.with_structured_output(_ResolvedQuestion)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Conversation so far:\n---\n{history}\n---"),
        ("human", "Follow-up: {question}"),
    ])
    chain = prompt | structured_llm
    try:
        result: _ResolvedQuestion = _invoke_with_retry(
            chain, {"history": history_text, "question": question}, label="Follow-up resolution call",
        )
        return result.standalone_question
    except Exception as exc:  # noqa: BLE001 - fail open, the raw question still works on its own
        logger.warning("Failed to resolve follow-up question %r against history: %s", question, exc)
        return question


class _MoEngageExclusive(BaseModel):
    moengage_only: bool = Field(
        description="True ONLY if answering this question needs NOTHING that a database query could "
        "provide - no revenue, no order count, no customer count, no spend, and no per-campaign opens/ "
        "clicks/CVR/unsubscribe/control-group metric either, since those now live in real, exact BigQuery "
        "tables too (see schema notes: moengage_campaigns_email/whatsapp/push, moengage_flows_summary) - "
        "prefer that real, queryable source over a chart summary whenever a question could be answered "
        "either way. This should be True mainly for genuine chart-only detail those tables don't capture "
        "(day-by-day trend over time, funnel step-by-step breakdown). False if answering it needs a "
        "database query for anything, even partially, or if you're genuinely not sure."
    )


def _is_moengage_exclusive(question: str, llm) -> bool:
    """Only called once MoEngage relevance is already confirmed (see
    gather_moengage_context) - decides whether the SQL agent needs to run
    AT ALL for this specific question, so a genuinely MoEngage-only
    question (e.g. 'what's our winback open rate') can skip BigQuery
    entirely instead of it running unconditionally on every question
    regardless of relevance. Real gap this fixes: MoEngage already had a
    real relevance gate (gather_moengage_context's pre-check) before this
    existed, but BigQuery's SQL agent had none at all - it built and ran
    the full ReAct loop on every single question, even ones with nothing
    for a database to answer, relying entirely on a prompt instruction
    ('don't run SQL as a substitute') to keep it from padding the answer
    with an irrelevant query - the same class of reliability gap this
    codebase's own deterministic safety nets exist to close everywhere
    else. Fails closed (False - query BigQuery too) on any error, since
    BigQuery is this system's broad default source and skipping it
    wrongly is a worse mistake than an unnecessary query."""
    structured_llm = llm.with_structured_output(_MoEngageExclusive)
    prompt = ChatPromptTemplate.from_messages([("human", "Question: {question}")])
    chain = prompt | structured_llm
    try:
        return _invoke_with_retry(chain, {"question": question}, label="MoEngage-exclusive check").moengage_only
    except Exception as exc:  # noqa: BLE001 - fail closed to the safer default (query BigQuery too)
        logger.warning("MoEngage-exclusive check failed for %r: %s - querying BigQuery too, to be safe.", question, exc)
        return False


def ask_analytics(
    question: str, conversation_history: Optional[list] = None, file_context: Optional[str] = None
) -> dict:
    """conversation_history, if given, is a list of {"question": ..., "answer": ...}
    dicts from earlier turns in the same thread/session - used only to resolve
    context ("the same", "that flow", "what about X instead"), never as a
    source of numbers. The model is explicitly told to always recompute the
    actual answer with a fresh query rather than reuse a figure from an
    earlier turn, so the number-grounding guardrail below still applies in
    full to every answer regardless of history.

    file_context, if given (a summary from file_context.summarize_files - a
    file uploaded alongside the question), is real data too - the model may
    cite numbers from it directly (the number-verification guardrail below
    checks against BOTH the SQL tool results AND this file context, so a
    figure genuinely from the uploaded file still passes)."""
    # No pre-check against the raw human question text here (there used to
    # be one) - real, live-caught false positive: "Where is the biggest
    # funnel drop-off?" got refused outright as an attempted write
    # operation, because \bDROP\b matches the ordinary English word "drop"
    # inside "drop-off" just as readily as it matches the SQL keyword. That
    # check added no real protection anyway - the actual guardrails are the
    # read-only DB connection (writes physically fail regardless of what
    # anyone asks) and _contains_write_operation(sql_query) below, checked
    # against the real SQL the agent actually tried to run, where these
    # keywords can't appear as innocuous prose the way they can in a human
    # question.
    llm = get_llm("ANALYTICS")

    # Resolve a follow-up ("what about for hair loss specifically") into a
    # complete standalone question BEFORE anything else - this is what the
    # source-selection reasoning below, the SQL agent, and number-
    # verification all actually work from, so topic continuity is settled
    # once up front rather than re-litigated (unreliably) inside one giant
    # combined prompt. effective_question is used for processing; `question`
    # (the human's literal text) is still what gets logged to history.
    effective_question = question
    if conversation_history:
        effective_question = _resolve_followup_question(question, conversation_history, llm)
        if effective_question != question:
            logger.info("Resolved follow-up %r -> %r", question, effective_question)

    # SOURCE SELECTION - reasoned, not a hard constraint to query both: real
    # gap this closes (previously) - MoEngage already had a genuine
    # relevance gate (gather_moengage_context's own pre-check), but BigQuery
    # had none at all, so the SQL agent ran unconditionally on every single
    # question regardless of whether a database had anything to do with it.
    # gather_moengage_context decides MoEngage relevance first (cheap
    # pre-check before paying the ~40-50s full-fetch cost); when it IS
    # relevant, _is_moengage_exclusive then decides whether BigQuery is
    # needed AT ALL for this specific question, so a genuinely
    # MoEngage-only question can skip the SQL agent entirely rather than
    # relying purely on a prompt instruction to keep it from padding the
    # answer with an irrelevant query.
    moengage_context, moengage_used, moengage_raw_summary, moengage_checked = gather_moengage_context(effective_question, llm)
    skip_bigquery = moengage_used and _is_moengage_exclusive(effective_question, llm)

    if skip_bigquery:
        raw_answer = sanitize_text(moengage_raw_summary)
        if _contains_pii(raw_answer):
            return {"answer": PII_BLOCKED_MESSAGE, "sql_query": "", "verified": False, "data_source": "moengage", "moengage_used": True}
        verified = _verify_numbers(raw_answer, moengage_context)
        answer = raw_answer if verified else (
            "I couldn't verify that figure - the number in my draft answer didn't trace back to a real chart result."
        )
        return {"answer": answer, "sql_query": "", "verified": verified, "data_source": "moengage", "moengage_used": True}

    # Only reachable once source selection above has already decided this
    # question genuinely needs the database - a BigQuery outage no longer
    # blocks a question the database was never going to be needed for.
    try:
        db = _get_db_cached()
    except Exception:
        logger.exception("BigQuery connection unavailable.")
        return {
            "answer": "I can't reach the live BigQuery connection right now, so I can't answer that. "
            "This usually means the credentials or IAM permission need attention.",
            "sql_query": "",
            "verified": False,
            "data_source": "bigquery",
            "moengage_used": moengage_used,
        }

    toolkit = SQLDatabaseToolkit(db=db, llm=llm)

    system_prefix = SYSTEM_PREFIX_TEMPLATE.format(
        today=date.today().isoformat(), schema_notes=BIGQUERY_SCHEMA_NOTES
    )

    agent_executor = create_sql_agent(
        llm=llm,
        toolkit=toolkit,
        agent_type="tool-calling",
        prefix=system_prefix,
        verbose=False,
        # max_iterations: real gap this raises - LangChain's own default (15)
        # was too low once real, verified, live-caught: a question needing
        # exploration across the 3 new real per-campaign MoEngage tables
        # (checking which real flow names exist, per table, before
        # aggregating) genuinely used every step correctly and still ran
        # out before producing a final answer, falling back to "couldn't
        # find data" despite already having computed the real number it
        # needed. More tables to reason over needs more budget to do it in,
        # not a smaller one. create_sql_agent's own top-level max_iterations
        # param, not agent_executor_kwargs - it passes max_iterations into
        # the AgentExecutor itself internally, so setting it again inside
        # agent_executor_kwargs is a genuine duplicate-keyword conflict
        # (caught live: TypeError on every single call once added there).
        max_iterations=25,
        agent_executor_kwargs={"return_intermediate_steps": True},
    )

    agent_input = (
        "Never copy a number from prior knowledge - always compute the answer with a fresh query:\n"
        + effective_question
    )
    if file_context:
        agent_input = (
            "A file was uploaded alongside this question - real data, safe to cite directly if it "
            "answers the question. Combine it with the database when relevant (e.g. the file lists "
            "products, the database has revenue for them):\n---\n" + file_context + "\n---\n\n" + agent_input
        )

    if moengage_used:
        agent_input = (
            "Real MoEngage campaign/engagement data relevant to this question, given to you directly "
            "below FROM THE CHART-BASED TOOL - a separate, narrower source than the real "
            "moengage_campaigns_email/whatsapp/push/flows_summary BigQuery tables (see schema notes), "
            "which now hold real, exact per-campaign opens/clicks/CVR/unsubscribe/control-group numbers - "
            "PREFER those tables via SQL for anything they cover (they give an exact queried number, not "
            "an LLM's read of a chart); use this chart-based context below only for genuine detail those "
            "tables don't have (day-by-day trend, funnel step-by-step breakdown). "
            "USE IT: if it answers something the tables genuinely don't, cite it directly - do not say "
            "that data isn't available if this context already shows it. Still run SQL for anything this "
            "doesn't cover (revenue, order counts, or the exact per-campaign metrics above), and "
            "combine both ONLY when the question genuinely needs both. A real, serious mistake this "
            "caused before: a plain 'how did automation perform this year' question, already fully and "
            "cleanly answered by one SQL revenue/order total, got padded out with several unrelated "
            "single-flow chart snippets (daily send/open counts for named flows the question never asked "
            "about) glued on with no stated relationship to the SQL total - a reader can't tell if those "
            "numbers are included in, separate from, or overlapping with the real total, which makes the "
            "whole answer impossible to trust. If the SQL total alone actually answers the question, stop "
            "there - do not append MoEngage detail just because it happens to be available. Only bring in "
            "a MoEngage number when it covers something SQL genuinely can't (opens/clicks/delivery rate), "
            "and when you do, state plainly which exact time period and population it covers so it's "
            "never confused with a different total in the same answer. Also: MoEngage/BigQuery 'ATM_' "
            "flows are automation broadly (WhatsApp AND email AND other channels) - never call them "
            "'automated email flows' collectively unless the question is specifically about the email "
            "channel; call them 'automated/CRM flows' otherwise.\n---\n"
            + moengage_context + "\n---\n\n" + agent_input
        )
    elif moengage_checked:
        # Real gap this closes: MoEngage's own real chart catalog was
        # genuinely checked (not skipped) and came back with nothing
        # relevant to THIS question - but without telling the SQL agent
        # that a real check happened, it has no way to know, and ends up
        # answering purely from BigQuery's own perspective ("no email-
        # engagement tables in the data warehouse") in a way that reads as
        # if MoEngage was never considered at all, when it genuinely was.
        # Caught live: "is list health deteriorating" got exactly this
        # vague, misleading answer even after a real MoEngage catalog
        # check confirmed no unsubscribe/complaint/bounce chart exists
        # anywhere in the real workspace - the honest, specific version of
        # that same true fact ("MoEngage doesn't track this specific
        # metric") is what should reach the final answer, not a generic
        # "not in the data warehouse" that implies no one looked.
        agent_input = (
            "MoEngage's real chart catalog was already checked for this question and found nothing "
            "relevant - the real reason, verbatim, is below. If your final answer touches anything that "
            "reason covers, state that SPECIFIC reason plainly (e.g. 'MoEngage doesn't track that as its "
            "own metric' or whatever the real reason says) - never say generically that the data 'isn't in "
            "the database' or 'isn't available' when a real, specific check already ran and found a real, "
            "specific reason.\n---\n" + moengage_context + "\n---\n\n" + agent_input
        )

    # Real, repeatedly-observed failure mode (same class already fixed for
    # every Copywriter/Sweeper call via invoke_with_retry): a transient Groq
    # tool-calling hiccup mid-ReAct-loop ("Failed to parse tool call
    # arguments as JSON", "attempted to call tool X which was not in
    # request.tools") used to crash this whole call with zero retry - the
    # caller's broad except still caught it and told the human "something
    # went wrong", but a real, answerable question shouldn't need a second
    # manual attempt just because of infra flakiness. Retry here too,
    # same principle, before giving up with a graceful message instead of
    # letting the exception propagate.
    result = None
    last_exc = None
    for attempt in range(3):
        try:
            result = agent_executor.invoke({"input": agent_input})
            break
        except Exception as exc:  # noqa: BLE001 - every attempt logged, final one falls through gracefully
            last_exc = exc
            logger.warning("Analytics SQL agent call failed (attempt %d/3): %s", attempt + 1, exc)
    data_source = "bigquery+moengage" if moengage_used else "bigquery"

    if result is None:
        logger.error("Analytics SQL agent failed after 3 attempts: %s", last_exc)
        return {
            "answer": "I hit a technical error trying to answer that - worth trying again in a moment, "
            "or rephrasing the question.",
            "sql_query": "",
            "verified": False,
            "data_source": data_source,
            "moengage_used": moengage_used,
        }

    raw_output = result.get("output", "").strip()
    if raw_output.lower().startswith("agent stopped due to") or not raw_output:
        # Blank output is a real, separate way this can go wrong from the
        # "agent stopped due to..." message (e.g. the agent's last step
        # produced no final text) - both get the same graceful fallback
        # rather than a blank answer silently passing every check below
        # (an empty string has no numbers to verify, so it would otherwise
        # come back marked verified=True).
        raw_output = (
            "I couldn't find data to answer that question with what's available in this database. "
            "It may be tracked in a different system, or the question may need to be more specific."
        )
    raw_answer = sanitize_text(raw_output)
    intermediate_steps = result.get("intermediate_steps", [])

    executed_queries = []
    tool_results_text = ""
    for action, observation in intermediate_steps:
        tool_name = getattr(action, "tool", "")
        tool_input = getattr(action, "tool_input", "")
        obs_text = str(observation)
        tool_results_text += "\n" + obs_text
        if "query" in tool_name.lower() and "checker" not in tool_name.lower() and "list" not in tool_name.lower() and "schema" not in tool_name.lower():
            query = tool_input.get("query") if isinstance(tool_input, dict) else tool_input
            if query:
                executed_queries.append(str(query))
                tool_results_text += "\n" + str(query)

    sql_query = "\n\n".join(executed_queries)

    if _contains_write_operation(sql_query):
        return {"answer": WRITE_BLOCKED_MESSAGE, "sql_query": sql_query, "verified": False, "data_source": data_source, "moengage_used": moengage_used}

    if _contains_pii(raw_answer):
        return {"answer": PII_BLOCKED_MESSAGE, "sql_query": sql_query, "verified": False, "data_source": data_source, "moengage_used": moengage_used}

    if file_context:
        tool_results_text += "\n" + file_context
    if moengage_used:
        tool_results_text += "\n" + moengage_context

    verified = _verify_numbers(raw_answer, tool_results_text)
    if verified:
        answer = raw_answer
    else:
        answer = "I couldn't verify that figure - the number in my draft answer didn't trace back to a query result."

    # Deterministic safety net for the campaign-name-sprawl failure mode - a
    # prompt instruction alone can't guarantee a probabilistic SQL agent
    # never builds an incomplete filter, so re-check independently rather
    # than trust it. Runs regardless of the `verified` outcome above (a
    # partial-match answer traces back to a real query result, so the
    # generic check alone wouldn't have caught it either).
    correction = _verify_campaign_family_total(effective_question, answer, sql_query)
    if correction:
        answer = correction
        verified = True

    # Second, broader safety net specifically for the flow_orders view -
    # unlike the check above (which reverse-engineers the agent's own SQL),
    # this independently re-derives the correct answer from the question
    # itself, so it catches mistakes the agent's SQL made that still look
    # internally consistent (e.g. correctly using is_flow_attributed but
    # forgetting new_product_category, or vice versa).
    view_correction = _verify_flow_orders_answer(effective_question, answer, sql_query)
    if view_correction:
        answer = view_correction
        verified = True

    return {
        "answer": answer,
        "sql_query": sql_query,
        "verified": verified,
        "data_source": data_source,
        "moengage_used": moengage_used,
    }

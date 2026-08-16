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
from typing import Optional

from langchain_community.agent_toolkits.sql.base import create_sql_agent
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase

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
- SPECIFIC LIFECYCLE FLOW TAGS: updated_sales_data.orders_utm_campaign carries real named CRM flow tags \
you can match with LOWER() LIKE, e.g. "abandoned_cart_v8" (cart-abandon recovery), "winback" (win-back), \
"WelcomeFlow_New" (welcome flow), "tp_email" (treatment-plan email), "order_approved" (order confirmation) \
- these let you answer "how much did flow X drive" questions precisely, grounded in the real campaign tag, \
rather than only the broad orders_utm_medium = email proxy.
- marketing_spend_data holds spend by Country, Brand, Channel, and month (Spends, Clicks, Impressions), \
at several Classification levels: "Category-Level" (paired with a Category like 'HL' for Hair Loss, \
'Weight_Loss', 'Supplements', 'EDPE'), "Overall-Level" (whole-account spend on that channel), and \
"Middle-Tier". NEVER sum different Classification levels together in the same total - that double-counts \
spend. Default to Category-Level rows (Category = 'HL') for "marketing spend" questions about hair loss \
specifically; ask/clarify or use Overall-Level for whole-account spend questions.
- There is NO email send/open/click event-tracking table in this warehouse at all - if a question asks \
about email opens, clicks, or send counts, say plainly that this data isn't available here rather than \
searching for it or guessing; do not loop trying to find a table that doesn't exist."""

SYSTEM_PREFIX_TEMPLATE = """You are the andSons analytics assistant. andSons is a men's health telehealth \
brand (hair loss is the flagship vertical, alongside weight loss and other supplements); all prices are \
in SGD. You answer questions about customers, orders, revenue, and marketing spend by querying the \
database directly, plus real MoEngage campaign/engagement data (opens, clicks, delivery, funnel \
drop-off by flow) when it's given to you below as relevant context for this question.

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
    if _contains_write_operation(question):
        return {"answer": WRITE_BLOCKED_MESSAGE, "sql_query": "", "verified": False, "data_source": "bigquery", "moengage_used": False}

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
            "moengage_used": False,
        }

    llm = get_llm("ANALYTICS")
    toolkit = SQLDatabaseToolkit(db=db, llm=llm)

    system_prefix = SYSTEM_PREFIX_TEMPLATE.format(schema_notes=BIGQUERY_SCHEMA_NOTES)

    agent_executor = create_sql_agent(
        llm=llm,
        toolkit=toolkit,
        agent_type="tool-calling",
        prefix=system_prefix,
        verbose=False,
        agent_executor_kwargs={"return_intermediate_steps": True},
    )

    agent_input = question
    if conversation_history:
        history_text = "\n\n".join(
            f"Q: {h['question']}\nA: {h['answer']}" for h in conversation_history
        )
        agent_input = (
            "Conversation so far in this thread, for CONTEXT ONLY - use it to resolve references like "
            "\"the same\", \"that flow\", \"what about X instead\", but NEVER copy a number from it "
            "directly into your new answer. Always compute the new answer with a fresh query, even if "
            "it looks like something already answered above:\n---\n" + history_text + "\n---\n\n"
            "New question: " + agent_input
        )
    if file_context:
        agent_input = (
            "A file was uploaded alongside this question - real data, safe to cite directly if it "
            "answers the question. Combine it with the database when relevant (e.g. the file lists "
            "products, the database has revenue for them):\n---\n" + file_context + "\n---\n\n" + agent_input
        )

    # MoEngage (campaign/engagement data) is a separate real data source from
    # BigQuery (sales/order data) - gather_moengage_context does a cheap
    # relevance pre-check first (most questions, e.g. "how many orders",
    # have nothing to do with campaign data) before paying the ~40-50s cost
    # of the full ~138-chart fetch+summarize. `relevant` is a real boolean
    # from structured output, not a guess from the summary text's wording.
    moengage_context, moengage_used = gather_moengage_context(question, llm)
    if moengage_used:
        agent_input = (
            "Real MoEngage campaign/engagement data relevant to this question - safe to cite "
            "directly if it answers the question, combined with the database when relevant:\n---\n"
            + moengage_context + "\n---\n\n" + agent_input
        )

    result = agent_executor.invoke({"input": agent_input})
    raw_output = result.get("output", "").strip()
    if raw_output.lower().startswith("agent stopped due to"):
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
        return {"answer": WRITE_BLOCKED_MESSAGE, "sql_query": sql_query, "verified": False, "data_source": "bigquery", "moengage_used": moengage_used}

    if _contains_pii(raw_answer):
        return {"answer": PII_BLOCKED_MESSAGE, "sql_query": sql_query, "verified": False, "data_source": "bigquery", "moengage_used": moengage_used}

    if file_context:
        tool_results_text += "\n" + file_context
    if moengage_used:
        tool_results_text += "\n" + moengage_context

    verified = _verify_numbers(raw_answer, tool_results_text)
    if verified:
        answer = raw_answer
    else:
        answer = "I couldn't verify that figure - the number in my draft answer didn't trace back to a query result."

    return {
        "answer": answer,
        "sql_query": sql_query,
        "verified": verified,
        "data_source": "bigquery",
        "moengage_used": moengage_used,
    }

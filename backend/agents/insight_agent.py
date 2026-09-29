"""Insight agent - turns a business signal ("OTC serum sales are down, write
something to fix it") into a grounded brief the Copywriter can act on.

Deliberately reuses ask_analytics() rather than querying BigQuery directly:
every guardrail already built for the Analytics Chat (write-blocking,
PII-blocking, number-verification against actual query results) applies for
free here too, instead of a second, less-hardened path to the same
warehouse.

MoEngage (campaign/engagement data) is optional context (moengage_summary.py,
shared with the Analytics Chat) - a cheap relevance pre-check first, then
(only if that says yes) every chart on every dashboard in the workspace
(~138 charts) reduced to one LLM summarization call, told which charts are
actually relevant to this question and to describe only what's really in
the data - never invent a metric that isn't there. If MoEngage isn't
configured, or isn't relevant to this signal, that's stated plainly rather
than silently omitted, so nobody mistakes "not connected"/"not relevant"
for "nothing interesting going on".

The brief text handed to the Copywriter is framed as STRATEGY CONTEXT, not
customer-facing content: generate_email() is responsible for making sure no
raw number, table name, or "engagement dropped 12%" style internal metric
ever leaks into the email itself.
"""
import re

import moengage_client
from moengage_summary import gather_moengage_context

from .analytics_agent import ask_analytics
from .llm_provider import get_llm

# Extra client-side safety net for investigate_patient() below, ON TOP OF
# analytics_agent.py's own PII guard (_contains_pii, which only checks for
# email addresses). A Singapore mobile number (+65 or bare, 8xxxxxxx/
# 9xxxxxxx) is real, directly-identifying PII that check would miss, and a
# per-patient lookup is exactly the higher-risk case where one could appear
# (a real customer service/order record). Not exhaustive - a name cannot be
# reliably caught by regex at all, which is why the prompt below forbids it
# explicitly and this is documented as a real, honest limitation, not a
# guarantee.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SG_PHONE_RE = re.compile(r"(\+?65[\s-]?)?[689]\d{3}[\s-]?\d{4}\b")

_PII_BLOCKED_MESSAGE = (
    "A patient-specific lookup was run, but its answer appeared to contain directly-identifying "
    "information (an email address or phone number) and was blocked before reaching this brief. "
    "Do not invent a substitute - proceed on the flow's own real catalog data alone, or ask a human "
    "to look this up manually if the personalization genuinely can't proceed without it."
)


def _contains_identifying_info(text: str) -> bool:
    return bool(_EMAIL_RE.search(text) or _SG_PHONE_RE.search(text))


def investigate(question: str, file_context: str = "") -> dict:
    """Investigate a business signal using real BigQuery data (always) and
    real MoEngage data (if configured). Returns a dict with the verified
    BigQuery answer/SQL for transparency, plus `brief_text` - the combined,
    clearly-labeled context to hand to generate_email()."""
    bigquery_question = (
        f"{question}\n\nQuantify this with real numbers: show the actual trend or comparison "
        "(e.g. recent period vs. the prior one) so it's clear whether this is really happening and by "
        "how much, broken down by whatever's most relevant (product, channel, flow) if the data supports it."
    )
    bq_result = ask_analytics(bigquery_question)

    moengage_notes, moengage_relevant, _, _ = gather_moengage_context(question, get_llm("ANALYTICS"))
    if not moengage_relevant:
        moengage_notes += " This brief is based on BigQuery sales data only."

    sections = [
        "BUSINESS SIGNAL RAISED: " + question,
        "",
        "REAL BIGQUERY FINDING (verified against live data):",
        bq_result["answer"],
    ]
    if not bq_result.get("verified"):
        sections.append(
            "(Note: this finding could not be fully verified against query results - treat it as "
            "directional only, not a hard number.)"
        )
    sections += ["", moengage_notes]

    if file_context:
        sections += ["", "DATA FROM A FILE UPLOADED WITH THIS REQUEST:", file_context]

    return {
        "brief_text": "\n".join(sections),
        "bigquery_answer": bq_result["answer"],
        "bigquery_sql": bq_result["sql_query"],
        "bigquery_verified": bq_result.get("verified", False),
        "moengage_configured": moengage_client.is_configured(),
    }


def investigate_patient(identifier: str, notes: str = "") -> dict:
    """Looks up ONE real, specific patient's real situation (for a
    'personalized' flow request - see copywriter_agent.EmailIntent), using
    the real per-brand warehouse access (order/subscription/journey detail
    - see analytics_agent.py's warehouse_query tool). This is deliberately
    NOT the same as investigate() above: the question is scoped to one
    identifier, not a business-wide signal, and the answer is held to a
    stricter bar before it's trusted - see _contains_identifying_info above.

    REAL, HONEST LIMIT: the extra client-side check here only catches an
    email address or a Singapore phone number pattern - it CANNOT reliably
    catch a customer's real name in prose (no regex can). This function
    relies on the prompt below explicitly forbidding a name, plus the
    downstream Copywriter's own leak-prevention instruction (never state a
    literal fact from an insight_brief verbatim), as defence in depth - not
    a guarantee. Treat this feature as needing real compliance review
    before high-volume production use, not a solved problem. Same shape as
    OVA Email's ova_email/agents/insight_agent.investigate_patient() - the
    two differ only in how they reach the shared analytics agent (a direct
    Python call here vs. HTTP there)."""
    identifier = identifier.strip()
    question = (
        f"Look up the real account/order/subscription/journey facts for the specific andSons patient "
        f"identified by: {identifier!r} (an order, customer, or subscription ID - look it up directly, "
        "do not guess or search by name). In your answer, describe ONLY behavioural/journey facts "
        "relevant to CRM messaging timing and angle - e.g. which real lifecycle stage they're likely at "
        "(e.g. The Valley, results-and-milestone), how long since their last order/consult, their "
        "plan/category type (general category only, e.g. 'hair loss treatment' - never a specific "
        "medicine/dose), and any real adherence or payment-status signal genuinely relevant to a CRM "
        "decision. Your answer must NEVER include their name, email address, phone number, physical "
        "address, date of birth, or any other directly-identifying field - describe their situation, not "
        "their identity. If the identifier can't be found, say so plainly rather than guessing."
        + (f"\n\nAdditional context for this lookup: {notes}" if notes else "")
    )
    result = ask_analytics(question)
    answer = result.get("answer", "")

    if _contains_identifying_info(answer):
        return {
            "brief_text": _PII_BLOCKED_MESSAGE, "found": False, "blocked_for_pii": True,
            "bigquery_answer": _PII_BLOCKED_MESSAGE, "bigquery_verified": False,
        }

    if not result.get("verified") or not answer:
        no_finding_text = (
            f"A lookup for patient identifier {identifier!r} did not return a verified, usable "
            "finding. Do not invent details about this specific patient - proceed on the flow's own "
            "real catalog data alone, and note in the note field that the personalization lookup "
            "didn't return anything usable."
        )
        return {
            "brief_text": no_finding_text, "found": False, "blocked_for_pii": False,
            "bigquery_answer": no_finding_text, "bigquery_verified": False,
        }

    return {
        "brief_text": (
            f"PERSONALIZATION LOOKUP for patient identifier {identifier!r} (real, verified finding - "
            f"identity-scrubbed, behavioural facts only):\n{answer}\n\n"
            "Use this to choose the right real flow/angle/timing for THIS specific person's real "
            "situation - never state a fact from this lookup as a literal, verbatim claim in the email "
            "copy (no 'we see you ordered X on Y date' type lines), and never imply the email exists "
            "because of an internal record the patient wouldn't expect you to reference. This shapes "
            "which real, already-approved message fits him, not what the email literally says."
        ),
        "found": True, "blocked_for_pii": False, "patient_answer": answer,
        "bigquery_answer": answer, "bigquery_verified": result.get("verified", False),
    }

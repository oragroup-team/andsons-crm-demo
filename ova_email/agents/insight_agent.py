"""Insight agent - turns a business signal, or a request for a flow build,
into a grounded brief the Copywriter can act on - using the SAME shared
analytics agent @andSons Analytics runs on, reached over HTTP
(analytics_client.py), not a local BigQuery/MoEngage connection (OVA Email
has none of its own - see analytics_client.py's own module docstring for
why this is a deliberate one-shared-agent architecture, not a gap).

REAL DIFFERENCE FROM ANDSONS' insight_agent.py: every question this module
sends is explicitly prefixed to name the OVA brand and Singapore market -
the shared agent's own schema notes default every query to
`Brand = 'AndSons'` unless told otherwise, so leaving that out would
silently investigate the wrong brand's data. No local MoEngage summarizing
happens here either (moengage_summary.py doesn't exist in this app) - the
shared agent already decides for itself, server-side, whether MoEngage data
is relevant to a given question (see its own `moengage_used` field in the
response), so this module just surfaces whatever it reports rather than
running a second, redundant relevance check.

The brief text handed to the Copywriter is framed as STRATEGY CONTEXT, not
customer-facing content - generate_email()'s own leak-prevention
instruction (_INSIGHT_BRIEF_INSTRUCTION) is what actually stops a raw
number or internal metric from reaching the email itself; this module only
gathers and labels the finding, it doesn't decide what's safe to say.
"""
import re

from analytics_client import ask_analytics

_OVA_BRAND_PREFIX = (
    "This question is about the OVA brand specifically (not andSons), Singapore market. "
)

# Extra client-side safety net for investigate_patient() below, ON TOP OF
# the shared analytics agent's own PII guard (analytics_agent._contains_pii,
# which only checks for email addresses - see analytics_client.py's module
# docstring). A Singapore mobile number (+65 or bare, 8xxxxxxx/9xxxxxxx) is
# real, directly-identifying PII that email-only check would miss, and a
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
    """Investigate a business signal (or a flow-build grounding question)
    using the real, shared analytics agent. Returns a dict with the
    verified answer/SQL for transparency, plus `brief_text` - the combined,
    clearly-labelled context to hand to generate_email()/generate_flow()."""
    branded_question = (
        f"{_OVA_BRAND_PREFIX}{question}\n\nQuantify this with real numbers where possible: show the actual "
        "trend or comparison (e.g. recent period vs. the prior one) so it's clear whether this is really "
        "happening and by how much, broken down by whatever's most relevant (flow, channel) if the data "
        "supports it."
    )
    result = ask_analytics(branded_question)

    sections = [
        "BUSINESS SIGNAL RAISED: " + question,
        "",
        "REAL FINDING (from the shared OVA/andSons analytics agent):",
        result["answer"],
    ]
    if not result.get("verified"):
        sections.append(
            "(Note: this finding could not be fully verified against query results - treat it as "
            "directional only, not a hard number.)"
        )
    if result.get("moengage_used"):
        sections.append("(This finding draws on real MoEngage campaign/engagement data, not sales data alone.)")

    if file_context:
        sections += ["", "DATA FROM A FILE UPLOADED WITH THIS REQUEST:", file_context]

    return {
        "brief_text": "\n".join(sections),
        "bigquery_answer": result["answer"],
        "bigquery_sql": result.get("sql_query"),
        "bigquery_verified": result.get("verified", False),
        "moengage_configured": True,  # the shared agent decides this server-side; always "reachable" from here
    }


def investigate_patient(identifier: str, notes: str = "") -> dict:
    """Looks up ONE real, specific patient's real situation (for a
    'personalized' flow request - see copywriter_agent.EmailIntent) via the
    SAME shared analytics agent, using its real per-brand warehouse access
    (order/subscription/journey detail - see analytics_client.py's module
    docstring on warehouse_query). This is deliberately NOT the same as
    investigate() above: the question is scoped to one identifier, not a
    business-wide signal, and the answer is held to a stricter bar before
    it's trusted - see _contains_identifying_info above.

    REAL, HONEST LIMIT: the extra client-side check here only catches an
    email address or a Singapore phone number pattern - it CANNOT reliably
    catch a customer's real name in prose (no regex can). This function
    relies on the prompt below explicitly forbidding a name, plus the
    downstream Copywriter's own leak-prevention instruction (never state a
    literal fact from an insight_brief verbatim), as defence in depth - not
    a guarantee. Treat this feature as needing real compliance review
    before high-volume production use, not a solved problem."""
    identifier = identifier.strip()
    question = (
        f"{_OVA_BRAND_PREFIX}Look up the real account/order/subscription/journey facts for the specific "
        f"patient identified by: {identifier!r} (an order, customer, or subscription ID - look it up "
        "directly, do not guess or search by name). In your answer, describe ONLY behavioural/journey "
        "facts relevant to CRM messaging timing and angle - e.g. which real lifecycle stage they're "
        "likely at, how long since their last order/consult, their plan/category type (general category "
        "only, e.g. 'weight loss programme' - never a specific medicine/dose), and any real adherence or "
        "payment-status signal genuinely relevant to a CRM decision. Your answer must NEVER include their "
        "name, email address, phone number, physical address, date of birth, or any other directly-"
        "identifying field - describe their situation, not their identity. If the identifier can't be "
        "found, say so plainly rather than guessing."
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
            f"PERSONALIZATION LOOKUP for patient identifier {identifier!r} (real, verified finding from "
            f"the shared analytics agent - identity-scrubbed, behavioural facts only):\n{answer}\n\n"
            "Use this to choose the right real flow/angle/timing for THIS specific person's real "
            "situation - never state a fact from this lookup as a literal, verbatim claim in the email "
            "copy (no 'we see you ordered X on Y date' type lines), and never imply the email exists "
            "because of an internal record the patient wouldn't expect you to reference. This shapes "
            "which real, already-approved message fits her, not what the email literally says."
        ),
        "found": True, "blocked_for_pii": False, "patient_answer": answer,
        "bigquery_answer": answer, "bigquery_verified": result.get("verified", False),
    }

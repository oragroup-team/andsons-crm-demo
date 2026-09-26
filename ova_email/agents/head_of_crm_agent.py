"""Head of CRM agent - owns the commercial objective for one OVA Singapore
flow build, before the Copywriter ever runs.

Ported structurally from the andSons backend's agents/head_of_crm_agent.py
(same CampaignBrief/TouchpointPlan shape, same "never state a number you
weren't given" discipline, same flow-synthesis mechanism for a request that
doesn't fit the real catalog, same EMAIL + WHATSAPP channels) - adapted for
OVA Singapore's own real categories and pricing shape.

REAL DIFFERENCE FROM THE ANDSONS VERSION: pricing here is per-flow
(`price_context`, a real dollar figure or None) rather than a single
verified-category gate, matching how flows.py/categories.py actually
encode OVA's real pricing rules.
"""
import logging
import re
from typing import List, Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from categories import DEFAULT_CATEGORY, VALID_CATEGORY_SLUGS
from flows import FLOW_BY_SLUG, VALID_FLOW_SLUGS
from session_store import save_synthesized_flow

from .llm_provider import get_llm, invoke_with_retry

logger = logging.getLogger("head_of_crm_agent")

SYSTEM_PROMPT = """You are the HEAD OF CRM for OVA Singapore (women's health telehealth). You own the \
revenue contribution of the CRM channel. You are commercial, decisive, and numbers-first. You do NOT write \
copy or design - you set the objective, the target segment, the KPI, the offer, AND the real touchpoint \
plan (how many sends, on which channel, in what order), then delegate to the Copywriter. Be decisive - one \
campaign, not options.

Never invent: base the brief only on the real flow information, the real signal (if any), and the real \
standing rules below - never a fabricated number, segment name, KPI target, or incentive that isn't \
grounded in what you're actually given. If the flow's own real data doesn't allow a price/offer, the offer \
angle is a genuine non-monetary motivator (a real product benefit, timing, reassurance), never an invented \
deal to compensate for that constraint.

NEVER STATE A NUMBER YOU WEREN'T GIVEN: the objective, KPI, offer angle, and success threshold must NEVER \
contain a specific percentage, conversion rate, target number, or statistic unless that exact figure \
appears verbatim in the REAL BUSINESS SIGNAL or THE PERSON'S OWN REQUEST below, OR is one of this flow's \
own real approved claims (see the flow brief's price/claim rules). No real number given means no number in \
the brief - describe the objective/threshold qualitatively instead. It is always better to be vague and \
honest than precise and made up.

NEVER A DISCOUNT PERCENTAGE: OVA_SG_CRM_AGENT_SCOPE.md's hard rule SS4.3 bans quoting a discount \
percentage in Singapore outbound, full stop - a real dollar saving is fine when the flow brief gives you \
one verbatim, a percentage never is, in the brief OR in what you tell the Copywriter to lead with.

CHANNELS - EMAIL AND WHATSAPP ONLY: OVA sends on exactly two real channels, email and WhatsApp - the same \
two andSons uses. Never propose a push/notification/SMS step in the cadence, regardless of what any \
reference material might suggest.

CADENCE - THIS IS YOUR CALL, NOT A FIXED TEMPLATE: the baseline cadence below (when this is a known flow) \
is a real, currently-designed target state - see flows.py's own docstring, it is NOT independently audited \
against a live OVA SG MoEngage account (that access does not exist yet), so treat it as a reasonable \
starting point, not a proven-correct rule. Decide the actual real touchpoint count, channel per step, \
timing, and per-step intent yourself, reasoning from three inputs, in this priority order:
1. If the person's own request below states an explicit number of touchpoints or channel mix, that \
instruction wins outright.
2. Otherwise, weigh the real performance data below (if any) and the baseline together.
3. If there's a baseline and neither of the above gives a reason to deviate, use it as-is.
For a flow with NO real baseline, decide the whole cadence yourself from the real signal, the audience, and \
sound CRM judgement. Ground this in the REAL distribution of OVA's own existing flows (see flows.py), not \
a vague sense of "a few touchpoints": of the 17 real flows in the catalog, 11 are a SINGLE touchpoint, 6 \
are two touchpoints, and NONE are three - a single, well-timed touchpoint is the real normal case, not the \
minimum. Add a second touchpoint only when there's a genuine, specific reason to nudge again (a pending \
decision that's easy to forget, a no-response case worth one gentle follow-up) - never default to two "to \
be safe" or because it sounds like a fuller campaign. Three touchpoints for a lifecycle send would be a \
real departure from every existing OVA flow - only do it if the signal specifically calls for it. Escalate \
gently, never pushy. Every step needs a channel (email or whatsapp only), a timing, and a one-line intent \
distinct from every other step's.

THE PERSON'S OWN REQUEST, VERBATIM:
{raw_request}

FLOW THIS CAMPAIGN IS FOR: {flow_name}
{flow_brief}

{signal_section}

LEARNED RULES FROM PAST HUMAN FEEDBACK (non-negotiable):
{learned_rules}
"""

_NO_SIGNAL_TEXT = (
    "No specific business signal was investigated for this request - it's a standard lifecycle send for "
    "this flow's real trigger, not a reaction to a live metric. (Note: OVA SG has no connected BigQuery/"
    "MoEngage data source yet - see this app's README - so this will always be the case until that's set up.)"
)
_NO_RAW_REQUEST_TEXT = "(no specific structural request on record - decide cadence from the baseline, data, and judgement alone)"

_VALID_CHANNELS = ("email", "whatsapp")


class TouchpointPlan(BaseModel):
    n: int = Field(description="This step's position in the sequence, starting at 1.")
    channel: Literal["email", "whatsapp"] = Field(
        description="Which real channel this step sends on. Only email and whatsapp are real OVA Singapore "
        "channels - there is no push/notification channel, do not propose one."
    )
    timing: str = Field(description="When this step fires, relative to the trigger (e.g. '+2 days') or a real condition.")
    intent: str = Field(description="One line: what this specific step is for - distinct from every other step, escalating gently.")


class CampaignBrief(BaseModel):
    objective: str = Field(description="The single concrete commercial objective for this campaign. Never a specific percentage/numeric target unless given to you above.")
    kpi: str = Field(description="The target metric this campaign is accountable to. Name the metric itself, never a number for it unless given to you above.")
    segment: str = Field(description="The precise audience segment this campaign targets.")
    lifecycle_stage: str = Field(description="Where this segment sits in the patient journey right now.")
    offer_angle: str = Field(description="The primary angle/motivator the Copywriter should lead with.")
    success_threshold: str = Field(description="What 'this worked' looks like, described qualitatively unless a real number was given above.")
    cadence: List[TouchpointPlan] = Field(description="The real touchpoint plan you decided on, in order. At least one touchpoint, no gaps or duplicate n values.")
    cadence_rationale: str = Field(description="One line: why this cadence.")

    def as_brief_text(self) -> str:
        cadence_lines = "\n".join(f"  {t.n}. {t.channel} ({t.timing}): {t.intent}" for t in self.cadence)
        return (
            f"OBJECTIVE: {self.objective}\nKPI: {self.kpi}\nSEGMENT: {self.segment}\n"
            f"LIFECYCLE STAGE: {self.lifecycle_stage}\nOFFER ANGLE: {self.offer_angle}\n"
            f"SUCCESS THRESHOLD: {self.success_threshold}\nCADENCE ({self.cadence_rationale}):\n{cadence_lines}"
        )


def _build_flow_brief(flow_slug: str) -> str:
    flow = FLOW_BY_SLUG.get(flow_slug)
    if flow is None:
        return f"Unknown flow slug {flow_slug!r} - brief conservatively from the name alone."
    price_note = (
        f"a real dollar figure MAY be offered: {flow['price_context']} - never a percentage, never invented"
        if flow.get("price_context") else "NO price or discount of any kind is permitted on this flow"
    )
    baseline_cadence = "\n".join(f"  {s['n']}. {s['channel']} ({s['timing']}): {s['intent']}" for s in flow["cadence"])
    return (
        f"Label: {flow['label']} (category: {flow['category']}, track: {flow['track']}, priority {flow['priority']})\n"
        f"Real trigger: {flow['trigger']}\n"
        f"Reader: {flow['audience']}\n"
        f"Existing goal: {flow['goal']}\n"
        f"The action this campaign points to: {flow['cta']}\n"
        f"Pricing rule: {price_note}\n"
        f"BASELINE CADENCE ({len(flow['cadence'])} touchpoint(s) - a designed target state, not an audited "
        f"one, see flows.py - your reference starting point, not a requirement):\n{baseline_cadence}"
    )


def brief_campaign(
    flow_name: str, signal_context: Optional[str] = None, learned_rules: Optional[str] = None,
    raw_request: Optional[str] = None,
) -> dict:
    llm = get_llm("HEAD_OF_CRM")
    structured_llm = llm.with_structured_output(CampaignBrief)

    flow_brief = _build_flow_brief(flow_name)
    signal_section = f"REAL BUSINESS SIGNAL THAT MOTIVATED THIS REQUEST:\n{signal_context}" if signal_context else _NO_SIGNAL_TEXT
    system_text = SYSTEM_PROMPT.format(
        flow_name=flow_name, flow_brief=flow_brief, signal_section=signal_section,
        learned_rules=learned_rules or "(none recorded yet)",
        raw_request=raw_request.strip() if raw_request else _NO_RAW_REQUEST_TEXT,
    )
    human_text = f"Brief the team for the {flow_name} campaign."

    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm

    result, last_exc = invoke_with_retry(chain, label="Head of CRM structured-output call")
    if result is None:
        logger.warning("Head of CRM failed after retrying (%s) - falling back to the flow's real baseline cadence unchanged.", last_exc)
        flow = FLOW_BY_SLUG.get(flow_name, {})
        fallback_cadence = [
            TouchpointPlan(n=s["n"], channel=s["channel"], timing=s["timing"], intent=s["intent"])
            for s in flow.get("cadence", [{"n": 1, "channel": "email", "timing": "immediate", "intent": "This flow's real trigger moment."}])
        ]
        fallback = CampaignBrief(
            objective=flow.get("goal", "Convert this send into the flow's intended action."),
            kpi="Conversion on this flow's real CTA",
            segment=flow.get("audience", "This flow's real documented audience."),
            lifecycle_stage=flow.get("label", flow_name),
            offer_angle=flow.get("goal", "Lead with the flow's documented goal."),
            success_threshold="Passes brand QA and reads as genuinely written for this moment.",
            cadence=fallback_cadence,
            cadence_rationale="Strategy-layer call failed - kept the flow's real baseline cadence unchanged rather than guess.",
        )
        return {"brief_text": fallback.as_brief_text(), "structured": fallback.model_dump(), "cadence": [t.model_dump() for t in fallback_cadence], "degraded": True}

    return {"brief_text": result.as_brief_text(), "structured": result.model_dump(), "cadence": [t.model_dump() for t in result.cadence], "degraded": False}


_SYNTHESIS_PROMPT = """You are the HEAD OF CRM for OVA Singapore (women's health telehealth). A real \
business problem was just investigated (or a person made a specific request) and none of the existing real \
flows in the catalog below genuinely fit it as the lifecycle moment to send from. Design a new one, in the \
exact same shape as every real OVA flow, grounded ONLY in the real signal below and sound CRM judgement - \
never invent a metric, segment, number, or product name not actually given to you.

EXISTING REAL FLOW CATALOG (for register/format reference and to confirm none of these actually fit):
{catalog}

REAL BUSINESS SIGNAL / REQUEST INVESTIGATED (the only grounding you have):
{signal}

THE PERSON'S OWN REQUEST, VERBATIM:
{raw_request}

DESIGN RULES (same as every real flow, non-negotiable):
- category: which of contraception / emergency_contraception / intimate_health / weight_loss this flow \
belongs to - pick the one the signal/request actually describes; never invent a fifth category.
- track: "rx" if this involves a doctor-led treatment/method decision (never name a product, never imply \
self-stop), "otc" if it's about a real non-prescription retail attachment, "neutral" if neither.
- SINGAPORE ONLY: never design a flow that could read as Malaysia-facing contraception content.
- cadence: ground this in the REAL distribution above (11 of 17 real flows are a SINGLE touchpoint, 6 are \
two, none are three) - a single touchpoint is the normal case, not a minimum to build up from. Add a \
second touchpoint only for a genuine, specific reason (a pending decision that's easy to forget, a \
no-response case worth one gentle follow-up), never by default or "to be thorough". Each touchpoint needs \
a real channel (email or whatsapp only - the same two andSons uses, no push/SMS) and a real relative \
timing or trigger condition - unless the person's own request states an explicit count/mix.
- price_context: null unless a real, specific dollar figure was actually given to you in the signal/ \
request above - never invent one, and never a percentage under any circumstance.
- Never name a specific prescription medicine or product - describe the category and benefit only.
- cta: a short natural action phrase in lower case (e.g. "book your consultation"), one that reads well as both an inline link inside an email sentence and a WhatsApp button - never a "Verb My Noun" button label.
"""


class SynthesizedFlow(BaseModel):
    label: str = Field(description="A short, real-sounding flow name in the same style as the existing catalog.")
    category: Literal[tuple(VALID_CATEGORY_SLUGS)] = Field(description="See DESIGN RULES above.")
    track: Literal["rx", "otc", "neutral"] = Field(description="See DESIGN RULES above.")
    trigger: str = Field(description="The real event/condition that would start this flow, phrased like the existing catalog's trigger field.")
    audience: str = Field(description="Who receives this flow and their real, specific situation right now - grounded in the investigated signal, not generic.")
    goal: str = Field(description="What this flow is trying to achieve for the reader and the business.")
    cta: str = Field(description="A short natural action phrase in lower case (e.g. 'book your consultation') - reads as both an inline email link and a WhatsApp button.")
    price_context: Optional[str] = Field(default=None, description="See DESIGN RULES above - null unless a real dollar figure was actually given.")
    allow_stat: bool = Field(default=False, description="Whether one of this category's real approved claims genuinely fits this flow's moment. Almost always false for a newly-synthesized flow unless the signal clearly supports it.")
    cadence: List[TouchpointPlan] = Field(description="The real touchpoint plan - see DESIGN RULES above.")
    rationale: str = Field(description="One line: why this flow structure, grounded in the real signal.")


def _slugify(label: str, existing_slugs: set) -> str:
    base = "custom_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    slug = base
    n = 2
    while slug in existing_slugs:
        slug = f"{base}_{n}"
        n += 1
    return slug


def synthesize_flow_for_signal(question: str, brief_text: str, raw_request: Optional[str] = None) -> Optional[str]:
    llm = get_llm("HEAD_OF_CRM")
    structured_llm = llm.with_structured_output(SynthesizedFlow)

    catalog = "\n".join(f"- {slug}: {flow['label']} ({flow['category']}, {flow['track']}) - {flow['audience']}" for slug, flow in FLOW_BY_SLUG.items())
    system_text = _SYNTHESIS_PROMPT.format(catalog=catalog, signal=brief_text, raw_request=(raw_request or question).strip())
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", "Design the new flow.")])
    chain = prompt | structured_llm

    result, last_exc = invoke_with_retry(chain, label="Flow synthesis structured-output call")
    if result is None:
        logger.warning("Flow synthesis failed after retrying (%s) - caller will fall back to asking a human.", last_exc)
        return None

    slug = _slugify(result.label, set(FLOW_BY_SLUG.keys()))
    new_flow = {
        "slug": slug, "label": result.label, "category": result.category, "track": result.track,
        "priority": max((f["priority"] for f in FLOW_BY_SLUG.values()), default=0) + 1,
        "trigger": result.trigger, "audience": result.audience, "goal": result.goal, "cta": result.cta,
        "price_context": result.price_context, "allow_stat": result.allow_stat,
        "cadence": [t.model_dump() for t in result.cadence],
    }
    FLOW_BY_SLUG[slug] = new_flow
    VALID_FLOW_SLUGS.append(slug)
    save_synthesized_flow(slug, new_flow)
    logger.info("Synthesized new flow %r (%s) for signal %r: %s", slug, result.label, question, result.rationale)
    return slug

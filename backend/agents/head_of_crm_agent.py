"""Head of CRM agent - the real, live n8n workflow's first pipeline step
(source: CRM_Email_Generation_Data/&SONS CRM Knowledge, "Agent Prompts -
CRM Team.md" and "andSons CRM — Project Overview & Runbook.md" - the exact
role, not something invented for this demo).

Owns the commercial objective. Runs BEFORE the Copywriter on every single
flow build (not just ones explicitly framed as a business signal) and
translates whatever is known - the flow's real audience/goal/trigger,
any live BigQuery/MoEngage signal that motivated this request, and the
standing Learned Rules from past human feedback (learned_rules_agent.py) -
into one decisive campaign brief the rest of the team executes against.
Never writes copy, never picks visuals - "commercial, decisive, and
numbers-first", per the real system prompt.

Real system's exact output shape - kept identical rather than reinvented:
OBJECTIVE / KPI / SEGMENT / LIFECYCLE STAGE / OFFER ANGLE / SUCCESS THRESHOLD.
"""
import logging
import re
from typing import List, Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from flows import FLOW_BY_SLUG, VALID_FLOW_SLUGS

from .llm_provider import get_llm, invoke_with_retry

logger = logging.getLogger("head_of_crm_agent")

SYSTEM_PROMPT = """You are the HEAD OF CRM for andSons Singapore (men's health / hair loss). You own the \
revenue contribution of the CRM channel. You are commercial, decisive, and numbers-first. You do NOT \
write copy or design - you set the objective, the target segment, the KPI, the offer, AND the real \
touchpoint plan (how many sends, on which channel, in what order), then delegate to the Copywriter. Be \
decisive - one campaign, not options.

Set a tight brief the rest of the team will execute against - one concrete objective, KPI, segment, \
lifecycle stage, offer angle, success threshold, and cadence. Never invent: base the brief only on the \
real flow information, the real signal (if any), and the real standing rules below - never a fabricated \
number, segment name, KPI target, discount, promo code, or incentive ("limited-time offer", "free \
shipping", a % off) that isn't grounded in what you're actually given - if the flow's own real data \
doesn't allow prices or offers, the offer angle is a genuine non-monetary motivator (urgency-free \
reassurance, timing, a real product benefit), never an invented deal to compensate for that constraint.

NEVER STATE A NUMBER YOU WEREN'T GIVEN (explicit correction, 2026-08-25, from Thalia after seeing a brief \
that invented "from 51.5% to at least 65%" out of nowhere): the objective, KPI, offer angle, and success \
threshold must NEVER contain a specific percentage, conversion rate, target number, or statistic unless \
that exact figure appears verbatim in the REAL BUSINESS SIGNAL or THE PERSON'S OWN REQUEST below. No \
real number given means no number in the brief, full stop - do not estimate one, do not compute a \
plausible-sounding target off it, do not restate an industry-typical rate as if it were this flow's own \
data. Describe the objective/threshold qualitatively instead (e.g. "meaningfully increase the share of \
recovered users who purchase a plan", "a clear, visible lift over where this flow sits today") - it is \
always better to be vague and honest than precise and made up.

CHANNELS - EMAIL AND WHATSAPP ONLY: andSons sends on exactly two channels, email and WhatsApp. Push \
notifications are not a real channel this brand uses (explicit instruction from Thalia, the real Head of \
CRM: "we only do whatsapp and email") - never propose a push/notification step in the cadence, regardless \
of what any older baseline or reference material might suggest.

CADENCE - THIS IS YOUR CALL, NOT A FIXED TEMPLATE: the baseline cadence below (when this is a known flow) \
is a reference starting point proven to work, not a rule you're bound to. Decide the actual real \
touchpoint count, channel per step, timing, and per-step intent yourself, reasoning from three inputs, in \
this priority order:
1. If the person's own request below states an explicit number of touchpoints or channel mix ("5 \
sequence mixed with WhatsApp and email", "just 2 emails"), that instruction wins outright - build exactly \
that, overriding the baseline entirely. Never quietly substitute the baseline when someone asked for \
something specific.
2. Otherwise, weigh the real performance data below (if any) and the baseline together - if the data \
suggests a different channel mix or cadence length would genuinely serve this send better, change it and \
say why in your reasoning; the baseline is a strong prior, not a cage.
3. If there's a baseline and neither of the above gives a reason to deviate, use it as-is - "different for \
its own sake" is not a goal.
For a flow with NO real baseline (a newly-described problem with nothing in the existing catalog), decide \
the whole cadence yourself from the real signal, the audience, and sound CRM judgement - normally 2 to 5 \
touchpoints for a lifecycle send, escalating gently, never pushy, matching how the other real andSons \
flows are paced. Every step needs a channel (email or whatsapp - see CHANNELS below), a timing (relative, \
e.g. "+2 days", or a real trigger condition), and a one-line intent distinct from every other step's.

THE PERSON'S OWN REQUEST, VERBATIM (read it for any explicit structural ask before deciding cadence):
{raw_request}

FLOW THIS CAMPAIGN IS FOR: {flow_name}
{flow_brief}

{signal_section}

LEARNED RULES FROM PAST HUMAN FEEDBACK (non-negotiable - apply every one before the Sweeper has to catch \
it):
{learned_rules}
"""

_NO_SIGNAL_TEXT = (
    "No specific business signal was investigated for this request - it's a standard lifecycle send for "
    "this flow's real trigger, not a reaction to a live metric."
)
_NO_RAW_REQUEST_TEXT = "(no specific structural request on record - decide cadence from the baseline, data, and judgement alone)"

_VALID_CHANNELS = ("email", "whatsapp")


class TouchpointPlan(BaseModel):
    n: int = Field(description="This step's position in the sequence, starting at 1.")
    channel: Literal["email", "whatsapp"] = Field(
        description="Which real channel this step sends on. Only email and whatsapp are real andSons "
        "channels - push notifications are never valid, do not propose one."
    )
    timing: str = Field(description="When this step fires, relative to the trigger (e.g. '+2 days') or a "
                         "real condition (e.g. 'on payment failure').")
    intent: str = Field(description="One line: what this specific step is for - distinct from every "
                         "other step, escalating gently across the sequence.")


class CampaignBrief(BaseModel):
    objective: str = Field(description="The single concrete commercial objective for this campaign. Never "
                            "include a specific percentage or numeric target unless that exact figure was "
                            "given to you in the real signal or the person's own request - describe it "
                            "qualitatively otherwise.")
    kpi: str = Field(description="The target metric this campaign is accountable to (revenue contribution, "
                      "conversion rate, reactivation count, etc.) - real and specific to this flow's goal. "
                      "Name the metric itself, never a specific number/percentage/target for it unless that "
                      "exact figure was given to you above.")
    segment: str = Field(description="The precise audience segment this campaign targets.")
    lifecycle_stage: str = Field(description="Where this segment sits in the patient journey right now.")
    offer_angle: str = Field(description="The primary angle/motivator the Copywriter should lead with.")
    success_threshold: str = Field(description="What 'this worked' looks like for this specific send - "
                                    "described qualitatively unless a real baseline/target number was given "
                                    "to you above, in which case use that exact number and no other.")
    cadence: List[TouchpointPlan] = Field(
        description="The real touchpoint plan you decided on, in order (n=1, 2, 3...) - see the CADENCE "
        "instructions above for how to decide this. At least one touchpoint, no gaps or duplicate n values."
    )
    cadence_rationale: str = Field(
        description="One line: why this cadence (matched the baseline / matched what was explicitly "
        "asked for / changed because of what the data showed) - so a human reviewer can see your reasoning, "
        "not just the result."
    )

    def as_brief_text(self) -> str:
        cadence_lines = "\n".join(
            f"  {t.n}. {t.channel} ({t.timing}): {t.intent}" for t in self.cadence
        )
        return (
            f"OBJECTIVE: {self.objective}\n"
            f"KPI: {self.kpi}\n"
            f"SEGMENT: {self.segment}\n"
            f"LIFECYCLE STAGE: {self.lifecycle_stage}\n"
            f"OFFER ANGLE: {self.offer_angle}\n"
            f"SUCCESS THRESHOLD: {self.success_threshold}\n"
            f"CADENCE ({self.cadence_rationale}):\n{cadence_lines}"
        )


def _build_flow_brief(flow_slug: str) -> str:
    flow = FLOW_BY_SLUG.get(flow_slug)
    if flow is None:
        return f"Unknown flow slug {flow_slug!r} - brief conservatively from the name alone."
    price_note = (
        "a real price MAY be offered (approved OTC catalogue only, never invented)" if flow["allow_price"]
        else "NO price, discount, or promo code is permitted on this flow - track it as no-price/no-payment"
    )
    baseline_cadence = "\n".join(
        f"  {s['n']}. {s['channel']} ({s['timing']}): {s['intent']}" for s in flow["cadence"]
    )
    return (
        f"Label: {flow['label']} (track: {flow['track']}, priority {flow['priority']})\n"
        f"Real trigger: {flow['trigger']}\n"
        f"Reader: {flow['audience']}\n"
        f"Existing goal: {flow['goal']}\n"
        f"Suggested CTA: {flow['cta']}\n"
        f"Pricing rule: {price_note}\n"
        f"BASELINE CADENCE ({len(flow['cadence'])} touchpoints - your reference starting point, not a "
        f"requirement, see CADENCE instructions above):\n{baseline_cadence}"
    )


def brief_campaign(
    flow_name: str, signal_context: Optional[str] = None, learned_rules: Optional[str] = None,
    raw_request: Optional[str] = None,
) -> dict:
    """Run the Head of CRM. Always called before the Copywriter, for every
    flow build - not only ones explicitly framed as a business signal
    (matching the real pipeline, where Head of CRM is unconditional).
    `signal_context` is the real investigated brief text (from
    insight_agent.investigate()) when this request was signal-driven;
    `learned_rules` is the current standing-rules text from
    learned_rules_agent.py; `raw_request` is the human's own literal
    request text (never paraphrased) so an explicit structural ask ("5
    sequence mixed with WhatsApp and email") reaches this decision
    directly rather than being diluted by an investigation summary.
    Returns a dict with the structured fields plus `brief_text` (the real
    field format the Copywriter expects) and `cadence` (the real
    touchpoint plan generate_flow() should build, overriding flows.py's
    static baseline). Fails safe: if the LLM call can't complete, falls
    back to a plain brief using the flow's own real baseline cadence
    unchanged, rather than blocking the whole pipeline on a strategy-layer
    failure."""
    llm = get_llm("HEAD_OF_CRM")
    structured_llm = llm.with_structured_output(CampaignBrief)

    flow_brief = _build_flow_brief(flow_name)
    signal_section = (
        f"REAL BUSINESS SIGNAL THAT MOTIVATED THIS REQUEST:\n{signal_context}"
        if signal_context else _NO_SIGNAL_TEXT
    )
    system_text = SYSTEM_PROMPT.format(
        flow_name=flow_name,
        flow_brief=flow_brief,
        signal_section=signal_section,
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
        return {
            "brief_text": fallback.as_brief_text(), "structured": fallback.model_dump(),
            "cadence": [t.model_dump() for t in fallback_cadence], "degraded": True,
        }

    return {
        "brief_text": result.as_brief_text(), "structured": result.model_dump(),
        "cadence": [t.model_dump() for t in result.cadence], "degraded": False,
    }


_SYNTHESIS_PROMPT = """You are the HEAD OF CRM for andSons Singapore (men's health / hair loss telehealth). \
A real business problem was just investigated and none of the existing real flows in the catalog below \
genuinely fit it as the lifecycle moment to send from. Design a new one, in the exact same shape as every \
real andSons flow, grounded ONLY in the real signal below and sound CRM judgement - never invent a metric, \
segment, or number not actually in the signal.

EXISTING REAL FLOW CATALOG (for register/format reference and to confirm none of these actually fit - \
never duplicate one of these, that would mean you should have picked it instead):
{catalog}

REAL BUSINESS SIGNAL INVESTIGATED (the only grounding you have - build the flow to actually address this):
{signal}

THE PERSON'S OWN REQUEST, VERBATIM:
{raw_request}

DESIGN RULES (same as every real flow, non-negotiable):
- track: "rx" if this involves prescription treatment decisions (never name a medicine, never a price on \
that track), "otc" if it's about an approved OTC product (Redensyl serum, Trio, Kit - only these, only \
their real prices, never invented ones), "neutral" if neither product nor price is the point.
- cadence: 2 to 5 touchpoints for a normal lifecycle send (fewer for something naturally single-touch like \
a no-show nudge), escalating gently, each with a distinct intent, each on a real channel (email or \
whatsapp only - andSons has no push channel) and a real relative timing or trigger condition - unless the \
person's own request above states an explicit count/channel mix, which overrides this and must be \
followed exactly.
- never a specific percentage, conversion rate, or numeric target anywhere in this flow's fields unless \
that exact figure appears verbatim in the real signal below.
- cta: verb + "My" + noun convention, matching the real flow catalog's register.
- Never a fabricated discount, promo code, or urgency device - if there's a genuine reason to offer a real \
approved OTC price, say so (allow_price=true); otherwise allow_price is false and the angle is a real, \
non-monetary motivator.
"""


class SynthesizedFlow(BaseModel):
    label: str = Field(description="A short, real-sounding flow name in the same style as the existing "
                        "catalog, e.g. 'Subscription Pause Recovery' - not a generic phrase like 'New Flow'.")
    track: Literal["rx", "otc", "neutral"] = Field(description="See DESIGN RULES above.")
    trigger: str = Field(description="The real event/condition that would start this flow in MoEngage, "
                          "phrased like the existing catalog's trigger field (an event name or condition).")
    audience: str = Field(description="Who receives this flow and their real, specific situation right now "
                           "- grounded in the investigated signal, not generic.")
    goal: str = Field(description="What this flow is trying to achieve for the reader and the business.")
    cta: str = Field(description="The primary call-to-action label - verb + My + noun convention.")
    allow_price: bool = Field(description="Whether a real approved OTC price may ever be mentioned on this "
                               "flow. False unless there's a genuine OTC-product reason.")
    allow_stat: bool = Field(default=False, description="Whether a real approved clinical stat may be "
                              "mentioned. Almost always false - this system has no verified clinical "
                              "statistic to cite (see the Copywriter's own compliance rules).")
    cadence: List[TouchpointPlan] = Field(description="The real touchpoint plan - see DESIGN RULES above.")
    rationale: str = Field(description="One line: why this flow structure, grounded in the real signal - "
                            "for a human reviewer to see your reasoning.")


def _slugify(label: str, existing_slugs: set) -> str:
    base = "custom_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    slug = base
    n = 2
    while slug in existing_slugs:
        slug = f"{base}_{n}"
        n += 1
    return slug


def synthesize_flow_for_signal(question: str, brief_text: str, raw_request: Optional[str] = None) -> Optional[str]:
    """Called when pick_flow_for_signal() found no existing catalog flow
    that genuinely fits an investigated business signal - rather than
    stopping to ask a human which flow to force it into, the Head of CRM
    designs a new one from the real signal, in the exact same shape as
    every real flow, and registers it into the live catalog (FLOW_BY_SLUG)
    under a generated slug so the rest of the pipeline (Copywriter,
    Sweeper, image renderers) works completely unchanged - none of them
    care whether a flow came from flows.py or was designed just now.
    Returns the new slug, or None if the design call itself failed (the
    caller falls back to asking a human, same as before this existed)."""
    llm = get_llm("HEAD_OF_CRM")
    structured_llm = llm.with_structured_output(SynthesizedFlow)

    catalog = "\n".join(
        f"- {slug}: {flow['label']} ({flow['track']}) - {flow['audience']}"
        for slug, flow in FLOW_BY_SLUG.items()
    )
    system_text = _SYNTHESIS_PROMPT.format(
        catalog=catalog, signal=brief_text,
        raw_request=(raw_request or question).strip(),
    )
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", "Design the new flow.")])
    chain = prompt | structured_llm

    result, last_exc = invoke_with_retry(chain, label="Flow synthesis structured-output call")
    if result is None:
        logger.warning("Flow synthesis failed after retrying (%s) - caller will fall back to asking a human.", last_exc)
        return None

    slug = _slugify(result.label, set(FLOW_BY_SLUG.keys()))
    new_flow = {
        "slug": slug,
        "label": result.label,
        "track": result.track,
        "priority": max((f["priority"] for f in FLOW_BY_SLUG.values()), default=0) + 1,
        "trigger": result.trigger,
        "audience": result.audience,
        "goal": result.goal,
        "cta": result.cta,
        "allow_price": result.allow_price,
        "allow_stat": result.allow_stat,
        "cadence": [t.model_dump() for t in result.cadence],
    }
    # FLOW_BY_SLUG is the same dict object every module imported - mutating
    # it here makes the new flow immediately visible everywhere (Copywriter,
    # Sweeper, this module's own _build_flow_brief) with no further wiring.
    FLOW_BY_SLUG[slug] = new_flow
    VALID_FLOW_SLUGS.append(slug)
    logger.info("Synthesized new flow %r (%s) for signal %r: %s", slug, result.label, question, result.rationale)
    return slug

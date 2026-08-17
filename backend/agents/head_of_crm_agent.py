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
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from flows import FLOW_BY_SLUG

from .llm_provider import get_llm, invoke_with_retry

logger = logging.getLogger("head_of_crm_agent")

SYSTEM_PROMPT = """You are the HEAD OF CRM for andSons Singapore (men's health / hair loss). You own the \
revenue contribution of the CRM channel. You are commercial, decisive, and numbers-first. You do NOT \
write copy or design - you set the objective, the target segment, the KPI, and the offer, then delegate \
to the Copywriter. Be decisive - one campaign, not options.

Set a tight brief the rest of the team will execute against - one concrete objective, KPI, segment, \
lifecycle stage, offer angle, and success threshold. Never invent: base the brief only on the real flow \
information, the real signal (if any), and the real standing rules below - never a fabricated number, \
segment name, KPI target, discount, promo code, or incentive ("limited-time offer", "free shipping", a \
% off) that isn't grounded in what you're actually given - if the flow's own real data doesn't allow \
prices or offers, the offer angle is a genuine non-monetary motivator (urgency-free reassurance, timing, \
a real product benefit), never an invented deal to compensate for that constraint.

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


class CampaignBrief(BaseModel):
    objective: str = Field(description="The single concrete commercial objective for this campaign.")
    kpi: str = Field(description="The target metric this campaign is accountable to (revenue contribution, "
                      "conversion rate, reactivation count, etc.) - real and specific to this flow's goal.")
    segment: str = Field(description="The precise audience segment this campaign targets.")
    lifecycle_stage: str = Field(description="Where this segment sits in the patient journey right now.")
    offer_angle: str = Field(description="The primary angle/motivator the Copywriter should lead with.")
    success_threshold: str = Field(description="What 'this worked' looks like for this specific send.")

    def as_brief_text(self) -> str:
        return (
            f"OBJECTIVE: {self.objective}\n"
            f"KPI: {self.kpi}\n"
            f"SEGMENT: {self.segment}\n"
            f"LIFECYCLE STAGE: {self.lifecycle_stage}\n"
            f"OFFER ANGLE: {self.offer_angle}\n"
            f"SUCCESS THRESHOLD: {self.success_threshold}"
        )


def _build_flow_brief(flow_slug: str) -> str:
    flow = FLOW_BY_SLUG.get(flow_slug)
    if flow is None:
        return f"Unknown flow slug {flow_slug!r} - brief conservatively from the name alone."
    price_note = (
        "a real price MAY be offered (approved OTC catalogue only, never invented)" if flow["allow_price"]
        else "NO price, discount, or promo code is permitted on this flow - track it as no-price/no-payment"
    )
    return (
        f"Label: {flow['label']} (track: {flow['track']}, priority {flow['priority']})\n"
        f"Real trigger: {flow['trigger']}\n"
        f"Reader: {flow['audience']}\n"
        f"Existing goal: {flow['goal']}\n"
        f"Suggested CTA: {flow['cta']}\n"
        f"Pricing rule: {price_note}\n"
        f"Real touchpoint count in this flow's cadence: {len(flow['cadence'])}"
    )


def brief_campaign(
    flow_name: str, signal_context: Optional[str] = None, learned_rules: Optional[str] = None,
) -> dict:
    """Run the Head of CRM. Always called before the Copywriter, for every
    flow build - not only ones explicitly framed as a business signal
    (matching the real pipeline, where Head of CRM is unconditional).
    `signal_context` is the real investigated brief text (from
    insight_agent.investigate()) when this request was signal-driven;
    `learned_rules` is the current standing-rules text from
    learned_rules_agent.py. Returns a dict with the structured fields plus
    `brief_text` - the exact real six-field format the Copywriter expects.
    Fails safe: if the LLM call can't complete, falls back to a plain
    brief built directly from the flow's own real metadata rather than
    blocking the whole pipeline on a strategy-layer failure."""
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
    )
    human_text = f"Brief the team for the {flow_name} campaign."

    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm

    result, last_exc = invoke_with_retry(chain, label="Head of CRM structured-output call")
    if result is None:
        logger.warning("Head of CRM failed after retrying (%s) - falling back to a plain flow-metadata brief.", last_exc)
        flow = FLOW_BY_SLUG.get(flow_name, {})
        fallback = CampaignBrief(
            objective=flow.get("goal", "Convert this send into the flow's intended action."),
            kpi="Conversion on this flow's real CTA",
            segment=flow.get("audience", "This flow's real documented audience."),
            lifecycle_stage=flow.get("label", flow_name),
            offer_angle=flow.get("goal", "Lead with the flow's documented goal."),
            success_threshold="Passes brand QA and reads as genuinely written for this moment.",
        )
        return {"brief_text": fallback.as_brief_text(), "structured": fallback.model_dump(), "degraded": True}

    return {"brief_text": result.as_brief_text(), "structured": result.model_dump(), "degraded": False}

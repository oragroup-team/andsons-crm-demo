"""Feedback node - NOT an LLM call for most of this file. Plain Python
functions that turn the Sweeper's reasons (or a human reviewer's free-text
note) into a structured correction instruction and re-invoke the Copywriter
with its ORIGINAL constraints plus that correction. Ported structurally
from the andSons backend's agents/feedback_node.py - same retry-loop
discipline (MAX_RETRIES, never just the raw reasons alone), same
structural-edit reasoning, same touchpoint-reference resolution.

REAL, DELIBERATE DIFFERENCE FROM THE ANDSONS VERSION: no live BigQuery/
MoEngage investigation. OVA_SG_CRM_AGENT_SCOPE.md SS8A/SS8D are explicit,
current blocking gaps - there is no OVA SG BigQuery access and no OVA SG
MoEngage workspace API credentials yet, so this file never calls a real
`investigate()`. Every seam where the andSons version would (run_flow_
pipeline's mandatory pre-brief investigation, run_insight_*_pipeline,
_run_live_data_lookup) is replaced with a plain, honest stub that says so
- never a fabricated finding standing in for real data. The moment OVA SG
gets real BigQuery/MoEngage access, wiring in a real insight_agent.py here
(copy andSons' unchanged - it has zero andSons-specific content) is a
small, contained change to just these stubs, not a rewrite.
"""
import difflib
import logging
from typing import List, Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from categories import DEFAULT_CATEGORY
from flows import FLOW_BY_SLUG

from .copywriter_agent import (
    _touchpoint_summary,
    flow_genuinely_fits,
    generate_email,
    generate_flow,
    generate_touchpoint,
    pick_flow_for_signal,
)
from .head_of_crm_agent import brief_campaign, synthesize_flow_for_signal
from .learned_rules_agent import distill_and_save_rule, learned_rules_text
from .llm_provider import get_llm, invoke_with_retry
from .sweeper_agent import sweep_email, sweep_whatsapp

logger = logging.getLogger("feedback_node")
logging.basicConfig(level=logging.INFO)

MAX_RETRIES = 2

_NO_LIVE_DATA_TEXT = (
    "No live business-data source is connected for OVA Singapore yet (OVA_SG_CRM_AGENT_SCOPE.md SS8A/SS8D "
    "- no OVA SG BigQuery access and no OVA SG MoEngage workspace credentials exist yet). This build is "
    "grounded in this flow's own real, documented audience/goal/catalog data only, not a live signal."
)


def format_correction(reasons: list) -> str:
    bullet_list = "\n".join(f"- {r}" for r in reasons)
    return (
        "The brand reviewer (Sweeper) rejected your last draft for these specific reasons:\n"
        f"{bullet_list}\n\n"
        "Fix EXACTLY these issues in the new draft. Do not introduce new content, new claims, new prices, "
        "new stats, or new structure beyond what's needed to fix them. Keep everything else about the "
        "previous draft's approach the same."
    )


def _diff_summary(before: Optional[str], after: str) -> str:
    if before is None:
        return "initial draft"
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0))
    changed_lines = [l for l in diff if l.startswith("+") or l.startswith("-")]
    if not changed_lines:
        return "no visible change"
    return f"{len(changed_lines)} line(s) changed"


def _run_pipeline_loop(flow_name: str, first_name: str, insight_brief: Optional[str] = None, category: str = DEFAULT_CATEGORY) -> dict:
    attempts = []
    previous_text = None
    correction = None
    final_email = None
    final_sweep = None
    needs_human_review = False

    for attempt_num in range(MAX_RETRIES + 1):
        email = generate_email(flow_name, first_name, correction=correction, insight_brief=insight_brief, category=category)
        rendered = email["rendered_text"]

        sweep = sweep_email(rendered, flow_name=flow_name, hero_info=email["content"], category=category)

        what_changed = _diff_summary(previous_text, rendered)
        log_entry = {
            "attempt": attempt_num + 1,
            "reasons_given_to_copywriter": correction is not None,
            "correction_reasons": None if attempt_num == 0 else attempts[-1]["sweeper_reasons"],
            "what_changed": what_changed,
            "sweeper_pass": sweep["pass"],
            "sweeper_reasons": sweep["reasons"],
            "sweeper_severity": sweep["severity"],
        }
        attempts.append(log_entry)
        logger.info("Attempt %d: pass=%s severity=%s reasons=%s changed=%s", attempt_num + 1, sweep["pass"], sweep["severity"], sweep["reasons"], what_changed)

        final_email = email
        final_sweep = sweep
        previous_text = rendered

        if sweep["pass"]:
            break
        if attempt_num == MAX_RETRIES:
            needs_human_review = True
            break
        correction = format_correction(sweep["reasons"])

    return {
        "email": final_email["content"], "rendered_text": final_email["rendered_text"], "flow_name": flow_name,
        "first_name": first_name, "passed": final_sweep["pass"], "needs_human_review": needs_human_review,
        "retries_used": len(attempts) - 1, "attempts": attempts,
    }


def run_email_pipeline(flow_name: str, first_name: str, file_context: str = "", category: str = DEFAULT_CATEGORY) -> dict:
    brief = f"DATA FROM A FILE UPLOADED WITH THIS REQUEST:\n{file_context}" if file_context else None
    return _run_pipeline_loop(flow_name, first_name, insight_brief=brief, category=category)


def run_insight_email_pipeline(question: str, first_name: str, flow_name: Optional[str] = None, file_context: str = "", category: str = DEFAULT_CATEGORY) -> dict:
    """No live investigation exists for OVA yet (see module docstring) -
    this always reports that plainly rather than fabricating a finding.
    Still runs the normal generation pipeline (grounded in the flow's own
    real data plus any uploaded file), just without a live-data brief."""
    brief = {"brief_text": _NO_LIVE_DATA_TEXT, "bigquery_answer": _NO_LIVE_DATA_TEXT, "bigquery_verified": False}
    if not flow_name:
        flow_name = pick_flow_for_signal(question, brief["brief_text"])
    if not flow_name:
        return {"needs_flow_clarification": True, "brief": brief, "question": question, "first_name": first_name}

    combined_context = brief["brief_text"] + (f"\n\nDATA FROM A FILE UPLOADED WITH THIS REQUEST:\n{file_context}" if file_context else "")
    result = _run_pipeline_loop(flow_name, first_name, insight_brief=combined_context, category=category)
    result["needs_flow_clarification"] = False
    result["insight_brief"] = brief
    result["signal_question"] = question
    return result


def _heroes_from_touchpoints(touchpoints: list, exclude_n: int) -> list:
    """Real (non-null) heroes used by email touchpoints OTHER than
    `exclude_n` in the same flow - feeds the Sweeper's hero-uniqueness
    check, same reasoning as andSons' feedback_node.py."""
    return [
        t["content"]["hero"] for t in touchpoints
        if t["n"] != exclude_n and t.get("channel") == "email" and t.get("content")
        and t["content"].get("hero") and t["content"]["hero"] != "none"
    ]


def _sweep_touchpoint(touchpoint: dict, flow_name: str, other_heroes: Optional[list] = None, human_feedback: Optional[str] = None, category: str = DEFAULT_CATEGORY, **_ignored) -> dict:
    if touchpoint["channel"] == "email":
        return sweep_email(touchpoint["rendered_text"], flow_name=flow_name, hero_info=touchpoint["content"], other_heroes=other_heroes, human_feedback=human_feedback, category=category)
    if touchpoint["channel"] == "whatsapp":
        return sweep_whatsapp(touchpoint["rendered_text"], flow_name=flow_name, human_feedback=human_feedback, category=category)
    raise ValueError(f"Unknown or unsupported channel for OVA: {touchpoint['channel']!r}")


def run_flow_pipeline(
    flow_name: str, file_context: str = "", insight_brief_text: Optional[str] = None,
    raw_request: Optional[str] = None, category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None,
) -> dict:
    """Generate the WHOLE real flow. Unlike andSons' run_flow_pipeline, this
    never runs a mandatory pre-brief live-data investigation - there is no
    real OVA SG data source to investigate yet (see module docstring). The
    Head of CRM still runs, briefed honestly on that gap plus this flow's
    own real catalog data and any uploaded file."""
    if insight_brief_text is None:
        insight_brief_text = _NO_LIVE_DATA_TEXT
        if file_context:
            insight_brief_text += f"\n\nDATA FROM A FILE UPLOADED WITH THIS REQUEST:\n{file_context}"

    crm_brief = brief_campaign(flow_name, signal_context=insight_brief_text, learned_rules=learned_rules_text(), raw_request=raw_request)

    brief_parts = [f"CAMPAIGN BRIEF (Head of CRM):\n{crm_brief['brief_text']}"]
    if file_context:
        brief_parts.append(f"DATA FROM A FILE UPLOADED WITH THIS REQUEST:\n{file_context}")
    brief = "\n\n".join(brief_parts)
    flow_result = generate_flow(flow_name, insight_brief=brief, cadence=crm_brief.get("cadence"), category=category, template_reference=template_reference)

    prior_summaries = []
    final_touchpoints = []
    any_needs_review = False

    for touchpoint in flow_result["touchpoints"]:
        step = {"n": touchpoint["n"], "channel": touchpoint["channel"], "timing": touchpoint["timing"], "intent": touchpoint["intent"]}
        correction = None
        attempts = 0
        needs_human_review = False

        if touchpoint.get("generation_failed"):
            touchpoint["passed"] = False
            touchpoint["sweeper_reasons"] = ["Content generation failed after retrying - reply with this step's number to try again, e.g. \"2: try again\"."]
            touchpoint["sweeper_severity"] = "major"
            any_needs_review = True
            final_touchpoints.append(touchpoint)
            prior_summaries.append(_touchpoint_summary(touchpoint))
            continue

        while True:
            other_heroes = _heroes_from_touchpoints(flow_result["touchpoints"], touchpoint["n"])
            sweep = _sweep_touchpoint(touchpoint, flow_name, other_heroes=other_heroes, category=category)

            logger.info("Flow %s touchpoint %d (%s, %s) attempt %d: pass=%s severity=%s reasons=%s", flow_name, touchpoint["n"], touchpoint["channel"], touchpoint["timing"], attempts + 1, sweep["pass"], sweep["severity"], sweep["reasons"])

            if sweep["pass"] or attempts >= MAX_RETRIES:
                needs_human_review = not sweep["pass"]
                touchpoint["passed"] = sweep["pass"]
                touchpoint["sweeper_reasons"] = sweep["reasons"]
                touchpoint["sweeper_severity"] = sweep["severity"]
                break

            correction = format_correction(sweep["reasons"])
            attempts += 1
            try:
                touchpoint = generate_touchpoint(flow_name, step, prior_summaries, correction=correction, insight_brief=brief, category=category, template_reference=template_reference)
            except RuntimeError as exc:
                logger.error("Touchpoint %d (%s) failed to regenerate after a correction: %s", step["n"], step["channel"], exc)
                touchpoint = {
                    "n": step["n"], "channel": step["channel"], "timing": step["timing"], "intent": step["intent"],
                    "content": None, "rendered_text": None, "generation_failed": True, "passed": False,
                    "sweeper_reasons": ["Content generation failed after retrying - reply with this step's number to try again, e.g. \"2: try again\"."],
                    "sweeper_severity": "major",
                }
                needs_human_review = True
                break

        any_needs_review = any_needs_review or needs_human_review
        final_touchpoints.append(touchpoint)
        prior_summaries.append(_touchpoint_summary(touchpoint))

    return {"flow_name": flow_name, "touchpoints": final_touchpoints, "needs_human_review": any_needs_review, "crm_brief": crm_brief}


def run_insight_flow_pipeline(
    question: str, flow_name: Optional[str] = None, file_context: str = "",
    raw_request: Optional[str] = None, category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None,
) -> dict:
    """No live investigation exists for OVA yet (see module docstring) -
    still picks/synthesizes a flow from the request's own text and the
    real catalog, and still builds the whole flow, just without a live
    signal brief."""
    brief = {"brief_text": _NO_LIVE_DATA_TEXT, "bigquery_answer": _NO_LIVE_DATA_TEXT, "bigquery_verified": False}

    if flow_name and not flow_genuinely_fits(question, flow_name):
        flow_name = None
    if not flow_name:
        flow_name = pick_flow_for_signal(question, brief["brief_text"])
    if not flow_name:
        flow_name = synthesize_flow_for_signal(question, brief["brief_text"], raw_request=raw_request)
    if not flow_name:
        return {"needs_flow_clarification": True, "brief": brief, "question": question}

    result = run_flow_pipeline(flow_name, file_context=file_context, insight_brief_text=brief["brief_text"], raw_request=raw_request or question, category=category, template_reference=template_reference)
    result["needs_flow_clarification"] = False
    result["insight_brief"] = brief
    result["signal_question"] = question
    return result


def resolve_flow_for_request(request_text: str, candidate_flow_name: Optional[str] = None) -> Optional[str]:
    if candidate_flow_name and flow_genuinely_fits(request_text, candidate_flow_name):
        return candidate_flow_name
    picked = pick_flow_for_signal(request_text, "(a direct build request describing exactly what's wanted, not an investigated live-data signal)")
    if picked and flow_genuinely_fits(request_text, picked):
        return picked
    return synthesize_flow_for_signal(request_text, request_text, raw_request=request_text)


class _LiveDataRequest(BaseModel):
    wants_live_data: bool = Field(
        description="True only if this feedback explicitly asks to check/use REAL, LIVE business data "
        "(BigQuery, MoEngage, 'the database', 'live data', 'actual numbers') to inform this revision - not "
        "just 'make it more convincing/compelling' on its own."
    )
    query_question: Optional[str] = Field(default=None, description="If wants_live_data: the actual business question to investigate, as one clean sentence. Null otherwise.")


def _resolve_live_data_request(feedback: str, flow_name: str) -> Optional[str]:
    """Classification only - never itself performs a lookup (see
    _run_live_data_lookup below for why that always fails closed for OVA
    right now)."""
    llm = get_llm("HEAD_OF_CRM", temperature=0.0)
    structured_llm = llm.with_structured_output(_LiveDataRequest)
    system_text = (
        f"This is real human revision feedback on the OVA '{flow_name}' CRM flow. Determine whether it "
        "genuinely asks for real, live business data to be checked and used for this revision, or is just "
        "a general creative request with no real data source implied."
    )
    escaped_feedback = feedback.replace("{", "{{").replace("}", "}}")
    human_text = f"Feedback: {escaped_feedback}"
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm
    result, last_exc = invoke_with_retry(chain, label="Live-data-request resolution call")
    if result is None:
        logger.warning("Live-data-request resolution failed (%s) - proceeding without a live lookup.", last_exc)
        return None
    if not result.wants_live_data:
        return None
    return result.query_question or f"(feedback asked for live data on the '{flow_name}' flow: {feedback})"


class _ImageChangeRequest(BaseModel):
    wants_image_change: bool = Field(
        description="True only if this feedback explicitly asks to change the PHOTO/IMAGE/HERO itself. "
        "False for absolutely everything else."
    )


def _feedback_asks_for_image_change(feedback: str) -> bool:
    """Real, reported problem this fixes (see andSons' feedback_node.py):
    the hero photo changing on its own initiative on a revision round that
    never asked for a different image. Fails closed to False - the safe
    default on an uncertain classification is to leave the image as-is."""
    llm = get_llm("HEAD_OF_CRM", temperature=0.0)
    structured_llm = llm.with_structured_output(_ImageChangeRequest)
    system_text = "This is real human revision feedback on an OVA email that already has a real hero photo. Determine whether it explicitly asks to change that photo/image, as opposed to any other kind of edit."
    escaped_feedback = feedback.replace("{", "{{").replace("}", "}}")
    human_text = f"Feedback: {escaped_feedback}"
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm
    result, last_exc = invoke_with_retry(chain, label="Image-change-request resolution call")
    if result is None:
        logger.warning("Image-change-request resolution failed (%s) - defaulting to NOT changing the hero.", last_exc)
        return False
    return result.wants_image_change


def _run_live_data_lookup(query_question: str) -> str:
    """Always fails closed and says so plainly - no `investigate()` exists
    for OVA yet (see module docstring, OVA_SG_CRM_AGENT_SCOPE.md SS8A/SS8D).
    Never fabricates a number to fill the gap."""
    return (
        "A live data lookup was requested for this revision, but " + _NO_LIVE_DATA_TEXT[0].lower() + _NO_LIVE_DATA_TEXT[1:] +
        " Do not invent a number or claim one was found - if the feedback specifically required real data, "
        "say plainly in the note field that no live data source is connected yet, and proceed without "
        "fabricating a substitute."
    )


def format_human_feedback(feedback: str, previous_draft: Optional[str] = None, feedback_history: Optional[list] = None, live_data_context: Optional[str] = None) -> str:
    sections = []
    if previous_draft:
        sections.append("Here is the CURRENT draft, exactly as it stands right now. Treat it as your starting point - you are editing this specific text, not writing a new email from a blank page:\n---\n" + previous_draft.strip() + "\n---")
    if live_data_context:
        sections.append(live_data_context)
    if feedback_history:
        numbered = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(feedback_history))
        sections.append("This draft already reflects ALL of this earlier feedback, applied in order. Every one of these must still hold true in your new draft:\n" + numbered)
    sections.append(
        "NEW feedback to apply now, on top of everything above:\n"
        f"{feedback.strip()}\n\n"
        "Implement this new feedback exactly and completely. If the feedback is about how something LOOKS "
        "or is LAID OUT, check whether this schema has a dedicated field for that exact thing and set it "
        "directly.\n\n"
        "HARD RULE if the feedback includes quoted example wording: that quoted text is a sketch of the "
        "KIND of line wanted, not a script - you are BANNED from reusing more than two or three consecutive "
        "words from inside those quotes anywhere in your output.\n\n"
        "Everything in the current draft that the feedback doesn't ask you to change should stay the same.\n\n"
        "This feedback overrides prior creative choices but never overrides compliance, the language rules, "
        "the flow's exact CTA label, Singapore-only, or invent-nothing. If the feedback genuinely asks for "
        "something one of those hard rules doesn't allow, apply the closest compliant interpretation and "
        "set the note field to one short, plain sentence naming the conflict.\n\n"
        "The note field is ONLY for THIS new feedback genuinely hitting a hard rule above - never about an "
        "earlier round, and never because a change simply has no dedicated field of its own."
    )
    return "\n\n".join(sections)


def revise_with_feedback(
    flow_name: str, first_name: str, feedback: str, previous_rendered_text: Optional[str] = None,
    feedback_history: Optional[list] = None, category: str = DEFAULT_CATEGORY,
    previous_content: Optional[dict] = None, template_reference: Optional[str] = None,
) -> dict:
    feedback_history = feedback_history or []

    query_question = _resolve_live_data_request(feedback, flow_name)
    live_data_context = _run_live_data_lookup(query_question) if query_question else None

    locked_hero = None
    if previous_content and previous_content.get("hero", "none") != "none" and not template_reference:
        if not _feedback_asks_for_image_change(feedback):
            locked_hero = {"hero": previous_content["hero"]}

    previous_draft = previous_rendered_text
    sweeper_correction = None
    email = None
    sweep = None

    for attempt_num in range(MAX_RETRIES + 1):
        correction = format_human_feedback(feedback, previous_draft=previous_draft, feedback_history=feedback_history, live_data_context=live_data_context)
        if sweeper_correction:
            correction = correction + "\n\n" + sweeper_correction

        email = generate_email(flow_name, first_name, correction=correction, category=category, locked_hero=locked_hero, template_reference=template_reference)
        rendered = email["rendered_text"]
        sweep = sweep_email(rendered, flow_name=flow_name, hero_info=email["content"], human_feedback=feedback, category=category)
        logger.info("Human feedback applied (flow=%s, round=%d, attempt=%d): %r | sweeper_pass=%s reasons=%s", flow_name, len(feedback_history) + 1, attempt_num + 1, feedback, sweep["pass"], sweep["reasons"])

        if sweep["pass"] or attempt_num == MAX_RETRIES:
            break
        previous_draft = rendered
        sweeper_correction = format_correction(sweep["reasons"])

    rendered = email["rendered_text"]
    return {
        "email": email["content"], "rendered_text": rendered, "flow_name": flow_name, "first_name": first_name,
        "feedback": feedback, "feedback_history": feedback_history + [feedback], "sweeper_pass": sweep["pass"],
        "sweeper_reasons": sweep["reasons"], "sweeper_severity": sweep["severity"],
    }


class _TouchpointReference(BaseModel):
    touchpoint_ns: List[int] = Field(default_factory=list, description="EVERY real step number this feedback should be applied to, confidently resolved. Several numbers when it explicitly asks for the same change across more than one step.")
    candidate_ns: List[int] = Field(default_factory=list, description="ONLY if touchpoint_ns is empty because the feedback is genuinely AMBIGUOUS between two or more touchpoints.")
    reason: str = Field(description="One short line explaining the resolution (or why it's ambiguous/no match).")


def _touchpoint_content_summary(t: dict) -> str:
    content = t.get("content")
    if content is None:
        return "(failed to generate - nothing to reference)"
    if t["channel"] == "email":
        block_types = [b.get("type") for b in (content.get("blocks") or [])]
        price_blocks = [b for b in (content.get("blocks") or []) if b.get("type") == "price_card"]
        return (
            f"a marketing email, top hero {content.get('hero', 'none')}, headline {content['subject']!r}, "
            f"subcopy {content.get('intro', '')!r}, its main CTA BUTTON reading {content.get('cta_text', '')!r}, "
            f"body content blocks: {block_types}, closing footer heading {content.get('closing_heading', '')!r} "
            f"with closing photo {content.get('closing_image', 'none')}"
            + (f", plus a price card ({price_blocks[0].get('label')}: {price_blocks[0].get('value')})" if price_blocks else "")
        )
    return (
        f"a WhatsApp message opening {content['hook_line']!r}, its one button reading {content['cta_text']!r}"
    )  # whatsapp


def resolve_touchpoint_reference(feedback_text: str, touchpoints: list) -> dict:
    lines = [f"- Step {t['n']} ({t['channel']}, {t['timing']}): {_touchpoint_content_summary(t)}" for t in touchpoints]
    llm = get_llm("HEAD_OF_CRM", temperature=0.0)
    structured_llm = llm.with_structured_output(_TouchpointReference)
    system_text = "Determine which of these real touchpoints a reviewer's feedback is about, using only their actual content below:\n" + "\n".join(lines)
    escaped_feedback = feedback_text.replace("{", "{{").replace("}", "}}")
    human_text = f"Reviewer feedback: {escaped_feedback}"
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm
    result, last_exc = invoke_with_retry(chain, label="Touchpoint reference resolution call")
    if result is None:
        logger.warning("Touchpoint reference resolution failed (%s) - falling back to asking directly.", last_exc)
        return {"touchpoint_ns": [], "candidate_ns": [], "reason": "resolution unavailable"}
    return result.model_dump()


def revise_flow_touchpoint(
    flow_name: str, touchpoints: list, touchpoint_n: int, feedback: str,
    feedback_history: Optional[list] = None, category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None,
) -> dict:
    feedback_history = feedback_history or []
    target = next((t for t in touchpoints if t["n"] == touchpoint_n), None)
    if target is None:
        raise ValueError(f"No touchpoint {touchpoint_n} in this flow (has {[t['n'] for t in touchpoints]}).")

    prior_summaries = [_touchpoint_summary(t) for t in touchpoints if t["n"] < touchpoint_n]
    step = {"n": target["n"], "channel": target["channel"], "timing": target["timing"], "intent": target["intent"]}
    other_heroes = _heroes_from_touchpoints(touchpoints, touchpoint_n)

    locked_hero = None
    target_content = target.get("content") or {}
    if target["channel"] == "email" and target_content.get("hero", "none") != "none" and not template_reference:
        if not _feedback_asks_for_image_change(feedback):
            locked_hero = {"hero": target_content["hero"]}

    query_question = _resolve_live_data_request(feedback, flow_name)
    live_data_context = _run_live_data_lookup(query_question) if query_question else None

    previous_draft = target["rendered_text"]
    sweeper_correction = None
    new_touchpoint = None
    sweep = None

    for attempt_num in range(MAX_RETRIES + 1):
        correction = format_human_feedback(feedback, previous_draft=previous_draft, feedback_history=feedback_history, live_data_context=live_data_context)
        if sweeper_correction:
            correction = correction + "\n\n" + sweeper_correction

        try:
            new_touchpoint = generate_touchpoint(flow_name, step, prior_summaries, correction=correction, category=category, locked_hero=locked_hero, template_reference=template_reference)
            sweep = _sweep_touchpoint(new_touchpoint, flow_name, other_heroes=other_heroes, human_feedback=feedback, category=category)
        except RuntimeError as exc:
            logger.error("Retry of touchpoint %d failed to generate: %s", touchpoint_n, exc)
            new_touchpoint = {
                "n": step["n"], "channel": step["channel"], "timing": step["timing"], "intent": step["intent"],
                "content": None, "rendered_text": None, "generation_failed": True, "passed": False,
                "sweeper_reasons": ["Content generation failed again - try replying with different wording, or try again in a moment."],
                "sweeper_severity": "major",
            }
            updated_touchpoints = [new_touchpoint if t["n"] == touchpoint_n else t for t in touchpoints]
            return {"flow_name": flow_name, "touchpoint": new_touchpoint, "touchpoints": updated_touchpoints, "feedback_history": feedback_history + [f"[step {touchpoint_n}] {feedback}"]}

        logger.info("Human feedback applied to flow=%s touchpoint=%d attempt=%d: %r | sweeper_pass=%s reasons=%s", flow_name, touchpoint_n, attempt_num + 1, feedback, sweep["pass"], sweep["reasons"])

        if sweep["pass"] or attempt_num == MAX_RETRIES:
            break
        previous_draft = new_touchpoint["rendered_text"]
        sweeper_correction = format_correction(sweep["reasons"])

    new_touchpoint["passed"] = sweep["pass"]
    new_touchpoint["sweeper_reasons"] = sweep["reasons"]
    new_touchpoint["sweeper_severity"] = sweep["severity"]

    if sweep["pass"]:
        distill_and_save_rule(feedback, new_touchpoint["rendered_text"] or "")

    updated_touchpoints = [new_touchpoint if t["n"] == touchpoint_n else t for t in touchpoints]
    return {"flow_name": flow_name, "touchpoint": new_touchpoint, "touchpoints": updated_touchpoints, "feedback_history": feedback_history + [f"[step {touchpoint_n}] {feedback}"]}


def revise_flow_touchpoints(
    flow_name: str, touchpoints: list, touchpoint_ns: List[int], feedback: str,
    feedback_history: Optional[list] = None, category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None,
) -> dict:
    feedback_history = feedback_history or []
    current_touchpoints = touchpoints
    revised = []
    for n in touchpoint_ns:
        step_result = revise_flow_touchpoint(flow_name, current_touchpoints, n, feedback, feedback_history=feedback_history, category=category, template_reference=template_reference)
        current_touchpoints = step_result["touchpoints"]
        feedback_history = step_result["feedback_history"]
        revised.append(step_result["touchpoint"])
    return {"flow_name": flow_name, "revised": revised, "touchpoints": current_touchpoints, "feedback_history": feedback_history}


class _TargetStep(BaseModel):
    n: int = Field(description="This step's position in the NEW sequence, starting at 1, contiguous, no gaps.")
    channel: Literal["email", "whatsapp"] = Field(description="The real channel for this step. OVA only ever sends on email and whatsapp - never propose anything else, there is no push/SMS channel.")
    timing: str = Field(description="When this step fires, relative to the trigger or its neighbours.")
    intent: str = Field(description="One line: what this step is for - distinct from every other step's.")
    from_existing_n: Optional[int] = Field(default=None, description="If this step's real content should carry over UNCHANGED from the CURRENT flow - the CURRENT step number it comes from. Only valid when the channel is the SAME. Null if this step is brand new, its channel just changed, or the feedback specifically asked to change what it says.")


class _StructuralRequest(BaseModel):
    changed: bool = Field(description="Whether this feedback asks to change the flow's real SHAPE at all. False if it's just a normal content edit to one existing step.")
    target_cadence: Optional[List[_TargetStep]] = Field(default=None, description="Only when changed=True: the flow's COMPLETE real touchpoint sequence as it should be AFTER applying this feedback.")
    reasoning: str = Field(description="One short line explaining the resolution (or why changed=False).")


def _resolve_structural_request(feedback_text: str, touchpoints: list, flow_name: str, feedback_history: Optional[list] = None) -> dict:
    lines = [f"- Step {t['n']} ({t['channel']}, {t['timing']}): {_touchpoint_content_summary(t)}" for t in touchpoints]
    history_text = "\n".join(f"- {h}" for h in feedback_history) if feedback_history else "(none yet - this is the first feedback on this flow)"
    llm = get_llm("HEAD_OF_CRM", temperature=0.0)
    structured_llm = llm.with_structured_output(_StructuralRequest)
    system_text = (
        f"This is real human feedback on the OVA '{flow_name}' flow, which currently has these real steps:\n"
        + "\n".join(lines) + "\n\nFULL FEEDBACK HISTORY ON THIS FLOW SO FAR, IN ORDER:\n" + history_text
        + "\n\nDetermine whether the NEW feedback below asks to change the flow's real shape, and if so, "
        "reason out its complete correct real touchpoint sequence afterwards."
    )
    escaped_feedback = feedback_text.replace("{", "{{").replace("}", "}}")
    human_text = f"New feedback: {escaped_feedback}"
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm
    result, last_exc = invoke_with_retry(chain, label="Structural-request resolution call")
    if result is None:
        logger.warning("Structural-request resolution failed (%s) - falling through to the content-edit path.", last_exc)
        return {"changed": False, "target_cadence": None, "reasoning": "resolution unavailable"}

    resolved = result.model_dump()
    if resolved["changed"] and not resolved["target_cadence"]:
        logger.warning("Structural resolution said changed=True but returned no target cadence - falling back to the content-edit path.")
        resolved["changed"] = False
        resolved["target_cadence"] = None
    elif resolved["changed"]:
        ordered = sorted(resolved["target_cadence"], key=lambda s: s["n"])
        for i, step in enumerate(ordered, start=1):
            step["n"] = i
        resolved["target_cadence"] = ordered
    return resolved


def _generate_and_sweep(flow_name: str, step: dict, prior_summaries: list, correction: str, other_heroes: Optional[list] = None, human_feedback: Optional[str] = None, category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None):
    working_correction = correction
    touchpoint = None
    sweep = None
    for attempt_num in range(MAX_RETRIES + 1):
        try:
            touchpoint = generate_touchpoint(flow_name, step, prior_summaries, correction=working_correction, category=category, template_reference=template_reference)
        except RuntimeError as exc:
            logger.error("New touchpoint %d (%s) failed to generate: %s", step["n"], step["channel"], exc)
            touchpoint = {"n": step["n"], "channel": step["channel"], "timing": step["timing"], "intent": step["intent"], "content": None, "rendered_text": None, "generation_failed": True}
            sweep = {"pass": False, "reasons": ["Content generation failed after retrying - reply again in a moment to retry this step."], "severity": "major"}
            break

        sweep = _sweep_touchpoint(touchpoint, flow_name, other_heroes=other_heroes, human_feedback=human_feedback, category=category)
        logger.info("New touchpoint %d (%s) attempt %d: pass=%s severity=%s reasons=%s", step["n"], step["channel"], attempt_num + 1, sweep["pass"], sweep["severity"], sweep["reasons"])
        if sweep["pass"] or attempt_num == MAX_RETRIES:
            break
        working_correction = correction + "\n\n" + format_correction(sweep["reasons"])

    return touchpoint, sweep


def apply_target_cadence(flow_name: str, touchpoints: list, target_cadence: list, feedback: str, feedback_history: Optional[list] = None, category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None) -> dict:
    feedback_history = feedback_history or []
    current_by_n = {t["n"]: t for t in touchpoints}
    ordered_target = sorted(target_cadence, key=lambda s: s["n"])

    new_touchpoints = []
    for target in ordered_target:
        source = current_by_n.get(target.get("from_existing_n")) if target.get("from_existing_n") else None
        if source is not None and source.get("channel") != target["channel"]:
            source = None
        if source is not None:
            new_touchpoints.append({**source, "n": target["n"], "timing": target["timing"], "intent": target["intent"]})
        else:
            new_touchpoints.append({"n": target["n"], "channel": target["channel"], "timing": target["timing"], "intent": target["intent"], "content": None, "rendered_text": None})

    generated_ns = []
    for i, tp in enumerate(new_touchpoints):
        if tp.get("content") is not None or tp.get("generation_failed"):
            continue
        prior_summaries = [_touchpoint_summary(t) for t in new_touchpoints[:i] if t.get("content") is not None]
        step = {"n": tp["n"], "channel": tp["channel"], "timing": tp["timing"], "intent": tp["intent"]}
        other_heroes = _heroes_from_touchpoints(new_touchpoints, tp["n"])
        correction = (
            "This step is being written/rewritten because a real reviewer's feedback on this flow requires "
            f"it. Their actual request: {feedback.strip()}\n\n"
            "Write this step's content fully fresh for its real place in the sequence - never reference its "
            "step number in the copy itself, just write for this specific moment in the customer's journey."
        )
        new_touchpoint, sweep = _generate_and_sweep(flow_name, step, prior_summaries, correction, other_heroes=other_heroes, human_feedback=feedback, category=category, template_reference=template_reference)
        new_touchpoint["passed"] = sweep["pass"]
        new_touchpoint["sweeper_reasons"] = sweep["reasons"]
        new_touchpoint["sweeper_severity"] = sweep["severity"]
        new_touchpoints[i] = new_touchpoint
        generated_ns.append(tp["n"])
        if sweep["pass"]:
            distill_and_save_rule(feedback, new_touchpoint.get("rendered_text") or "")

    old_ns = sorted(current_by_n.keys())
    kept_ns = sorted(t.get("from_existing_n") for t in ordered_target if t.get("from_existing_n"))
    removed_ns = [n for n in old_ns if n not in kept_ns]
    summary_bits = []
    if generated_ns:
        summary_bits.append(f"wrote step(s) {generated_ns}")
    if removed_ns:
        summary_bits.append(f"removed step(s) {removed_ns}")
    change_summary = "; ".join(summary_bits) or "reordered with no content changes"

    return {"flow_name": flow_name, "touchpoints": new_touchpoints, "feedback_history": feedback_history + [f"[restructured: {change_summary}] {feedback}"]}

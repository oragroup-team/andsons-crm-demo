"""Feedback node — NOT an LLM call. A plain Python function that turns the
Sweeper's reasons into a structured correction instruction and re-invokes the
Copywriter agent with its ORIGINAL constraints plus that correction.

This is the fix for the real bug where revision loops caused hallucinated /
off-brand output: every retry gets the full original system prompt again,
never just the raw reasons on their own. Capped at 2 retries — on a 3rd
failure the pipeline returns needs_human_review=True instead of looping again.
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
from .insight_agent import investigate, investigate_patient
from .learned_rules_agent import distill_and_save_rule, learned_rules_text
from .llm_provider import get_llm, invoke_with_retry
from .sweeper_agent import sweep_email, sweep_push, sweep_whatsapp

logger = logging.getLogger("feedback_node")
logging.basicConfig(level=logging.INFO)

MAX_RETRIES = 2


def format_correction(reasons: list) -> str:
    """Turn Sweeper's raw reasons into a structured correction instruction.
    Never passed to the Copywriter alone — always appended to the full
    original system prompt/constraints by generate_email()."""
    bullet_list = "\n".join(f"- {r}" for r in reasons)
    return (
        "The brand reviewer (Sweeper) rejected your last draft for these specific reasons:\n"
        f"{bullet_list}\n\n"
        "Fix EXACTLY these issues in the new draft. Do not introduce new content, new claims, "
        "new prices, new stats, or new structure beyond what's needed to fix them. Keep everything "
        "else about the previous draft's approach the same."
    )


def _diff_summary(before: Optional[str], after: str) -> str:
    if before is None:
        return "initial draft"
    diff = list(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        )
    )
    changed_lines = [l for l in diff if l.startswith("+") or l.startswith("-")]
    if not changed_lines:
        return "no visible change"
    return f"{len(changed_lines)} line(s) changed"


def _run_pipeline_loop(
    flow_name: str, first_name: str, insight_brief: Optional[str] = None, category: str = DEFAULT_CATEGORY,
) -> dict:
    """Shared Copywriter -> Sweeper -> Feedback retry loop (capped at
    MAX_RETRIES), used by both the plain flow+name pipeline and the
    insight-driven one. `insight_brief`, when given, is passed to
    generate_email() on every attempt (including retries) so a correction
    round never loses the strategy context that motivated the email."""
    attempts = []
    previous_text = None
    correction = None
    final_email = None
    final_sweep = None
    needs_human_review = False

    for attempt_num in range(MAX_RETRIES + 1):  # attempt 0 = first draft, 1 and 2 = retries
        email = generate_email(flow_name, first_name, correction=correction, insight_brief=insight_brief, category=category)
        rendered = email["rendered_text"]

        sweep = sweep_email(rendered, flow_name=flow_name, hero_info=email["content"], category=category)

        what_changed = _diff_summary(previous_text, rendered)
        log_entry = {
            "attempt": attempt_num + 1,
            "reasons_given_to_copywriter": correction is not None,
            "correction_reasons": (
                None if attempt_num == 0 else attempts[-1]["sweeper_reasons"]
            ),
            "what_changed": what_changed,
            "sweeper_pass": sweep["pass"],
            "sweeper_reasons": sweep["reasons"],
            "sweeper_severity": sweep["severity"],
        }
        attempts.append(log_entry)
        logger.info(
            "Attempt %d: pass=%s severity=%s reasons=%s changed=%s",
            attempt_num + 1,
            sweep["pass"],
            sweep["severity"],
            sweep["reasons"],
            what_changed,
        )

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
        "email": final_email["content"],
        "rendered_text": final_email["rendered_text"],
        "flow_name": flow_name,
        "first_name": first_name,
        "passed": final_sweep["pass"],
        "needs_human_review": needs_human_review,
        "retries_used": len(attempts) - 1,
        "attempts": attempts,
    }


def run_email_pipeline(flow_name: str, first_name: str, file_context: str = "", category: str = DEFAULT_CATEGORY) -> dict:
    """Copywriter -> Sweeper -> Feedback loop, capped at MAX_RETRIES retries.
    file_context, if given (a summary from a file uploaded alongside the
    request), is passed through as strategy context via the same
    leak-prevention path as an insight brief - see generate_email()'s
    _INSIGHT_BRIEF_INSTRUCTION."""
    brief = f"DATA FROM A FILE UPLOADED WITH THIS REQUEST:\n{file_context}" if file_context else None
    return _run_pipeline_loop(flow_name, first_name, insight_brief=brief, category=category)


def run_insight_email_pipeline(
    question: str, first_name: str, flow_name: Optional[str] = None, file_context: str = "",
    category: str = DEFAULT_CATEGORY,
) -> dict:
    """Investigate a business signal (real BigQuery data, plus real MoEngage
    data if configured) and write an email addressing it, through the same
    Copywriter -> Sweeper -> Feedback loop as run_email_pipeline. If
    `flow_name` isn't given, it's picked from the investigation findings via
    pick_flow_for_signal(); if nothing genuinely fits, returns
    needs_flow_clarification=True instead of forcing a weak match."""
    brief = investigate(question, file_context=file_context)

    if not flow_name:
        flow_name = pick_flow_for_signal(question, brief["brief_text"])
    if not flow_name:
        return {
            "needs_flow_clarification": True,
            "brief": brief,
            "question": question,
            "first_name": first_name,
        }

    result = _run_pipeline_loop(flow_name, first_name, insight_brief=brief["brief_text"], category=category)
    result["needs_flow_clarification"] = False
    result["insight_brief"] = brief
    result["signal_question"] = question
    return result


def _heroes_from_touchpoints(touchpoints: list, exclude_n: int) -> list:
    """Real (non-null) heroes used by email touchpoints OTHER than
    `exclude_n` in the same flow - feeds the Sweeper's Flow Fidelity hero-
    uniqueness check (see sweeper_agent.sweep_email's `other_heroes`)."""
    return [
        t["content"]["hero"]
        for t in touchpoints
        if t["n"] != exclude_n
        and t.get("channel") == "email"
        and t.get("content")
        and t["content"].get("hero")
        and t["content"]["hero"] != "none"
    ]


def _sweep_touchpoint(
    touchpoint: dict, flow_name: str, other_heroes: Optional[list] = None, human_feedback: Optional[str] = None,
    category: str = DEFAULT_CATEGORY,
) -> dict:
    """Single dispatch point for "sweep this touchpoint with whatever
    channel's rules apply" - mirrors copywriter_agent.generate_touchpoint().

    `human_feedback`, when this touchpoint is a human-driven revision (not
    the automatic first-draft pipeline), is the actual reviewer request
    that produced this candidate - without it the Sweeper has no way to
    judge a "only when a reviewer explicitly asks" LEARNED CHECK, since it
    otherwise only ever sees the rendered output."""
    if touchpoint["channel"] == "email":
        return sweep_email(
            touchpoint["rendered_text"], flow_name=flow_name, hero_info=touchpoint["content"],
            other_heroes=other_heroes, human_feedback=human_feedback, category=category,
        )
    if touchpoint["channel"] == "whatsapp":
        return sweep_whatsapp(touchpoint["rendered_text"], flow_name=flow_name, human_feedback=human_feedback, category=category)
    if touchpoint["channel"] == "push":
        return sweep_push(touchpoint["rendered_text"], flow_name=flow_name, human_feedback=human_feedback, category=category)
    raise ValueError(f"Unknown channel: {touchpoint['channel']!r}")


def run_flow_pipeline(
    flow_name: str, file_context: str = "", insight_brief_text: Optional[str] = None,
    raw_request: Optional[str] = None, category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None,
) -> dict:
    """Generate the WHOLE real flow - every touchpoint in its real cadence
    - not just one email. Each touchpoint goes through its own Sweeper QA
    gate; a touchpoint that fails is regenerated (capped at MAX_RETRIES) in
    place, using the same correction-loop principle as _run_pipeline_loop,
    without discarding or re-generating the touchpoints around it
    (regenerating the whole sequence over one failing touchpoint would also
    throw away good passing ones, and would risk small wording drift
    between runs).

    `raw_request`, when given, is the human's own literal text (never
    paraphrased) - passed straight to the Head of CRM so an explicit
    structural ask ("5 sequence mixed with WhatsApp and email") reaches
    the cadence decision directly. `template_reference` (from
    agents.template_agent.analyze_template_image, when a reference image
    was uploaded with this request) is a real design instruction passed
    straight to the Copywriter - see copywriter_agent._TEMPLATE_REFERENCE_
    INSTRUCTION."""
    # EVERY flow build is grounded in real, live data before anything is
    # decided - not only ones explicitly framed as a business signal. A
    # direct 'write the winback flow' request still gets a real BigQuery/
    # MoEngage check first, same as an insight-driven one; the only
    # difference is what question gets investigated (the human's own
    # signal question there, a generated one about this flow's real
    # performance here). Skipped only when a caller already did this
    # investigation itself (run_insight_flow_pipeline) and handed the
    # result in, so this never double-queries the same request.
    if insight_brief_text is None:
        flow_meta = FLOW_BY_SLUG.get(flow_name)
        flow_label = flow_meta["label"] if flow_meta else flow_name
        default_question = (
            f"How is the andSons '{flow_label}' flow performing right now, and what does the real "
            "data suggest about its cadence, channel mix, and messaging angle?"
        )
        investigation = investigate(default_question, file_context=file_context)
        insight_brief_text = investigation["brief_text"]

    # Head of CRM runs FIRST, unconditionally, on every flow build - not
    # only ones explicitly framed as a business signal (matching the real
    # pipeline: Head of CRM -> Copywriter is fixed, never skipped). It
    # turns whatever's known (the flow's real metadata, the live signal
    # above, and the human's own literal request) into one decisive
    # commercial brief AND real touchpoint plan the Copywriter executes
    # against - flows.py's cadence is this decision's baseline, not a
    # requirement (see head_of_crm_agent.py's CADENCE instructions).
    crm_brief = brief_campaign(
        flow_name, signal_context=insight_brief_text, learned_rules=learned_rules_text(),
        raw_request=raw_request,
    )

    brief_parts = [f"CAMPAIGN BRIEF (Head of CRM):\n{crm_brief['brief_text']}"]
    if file_context:
        brief_parts.append(f"DATA FROM A FILE UPLOADED WITH THIS REQUEST:\n{file_context}")
    brief = "\n\n".join(brief_parts)
    flow_result = generate_flow(
        flow_name, insight_brief=brief, cadence=crm_brief.get("cadence"), category=category,
        template_reference=template_reference,
    )

    prior_summaries = []
    final_touchpoints = []
    any_needs_review = False

    for touchpoint in flow_result["touchpoints"]:
        step = {"n": touchpoint["n"], "channel": touchpoint["channel"], "timing": touchpoint["timing"], "intent": touchpoint["intent"]}
        correction = None
        attempts = 0
        needs_human_review = False

        # generate_flow() already turned an exhausted-retry failure into a
        # placeholder (content/rendered_text = None) rather than raising -
        # nothing to sweep here, and reusing revise_flow_touchpoint() is
        # how a person retries it (see app.py's "reply to retry" message).
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

            logger.info(
                "Flow %s touchpoint %d (%s, %s) attempt %d: pass=%s severity=%s reasons=%s",
                flow_name, touchpoint["n"], touchpoint["channel"], touchpoint["timing"],
                attempts + 1, sweep["pass"], sweep["severity"], sweep["reasons"],
            )

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
                # Same real failure mode as generate_flow()'s own retry
                # exhaustion, just hit during a Sweeper-triggered
                # correction instead of the first draft - same placeholder
                # fallback, not a crash.
                logger.error("Touchpoint %d (%s) failed to regenerate after a correction: %s", step["n"], step["channel"], exc)
                touchpoint = {
                    "n": step["n"], "channel": step["channel"], "timing": step["timing"], "intent": step["intent"],
                    "content": None, "rendered_text": None, "hero": None, "generation_failed": True,
                    "passed": False,
                    "sweeper_reasons": ["Content generation failed after retrying - reply with this step's number to try again, e.g. \"2: try again\"."],
                    "sweeper_severity": "major",
                }
                needs_human_review = True
                break

        any_needs_review = any_needs_review or needs_human_review
        final_touchpoints.append(touchpoint)
        prior_summaries.append(_touchpoint_summary(touchpoint))

    return {
        "flow_name": flow_name,
        "touchpoints": final_touchpoints,
        "needs_human_review": any_needs_review,
        "crm_brief": crm_brief,
    }


def run_insight_flow_pipeline(
    question: str, flow_name: Optional[str] = None, file_context: str = "",
    raw_request: Optional[str] = None, category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None,
) -> dict:
    """Investigate a business signal, then generate the WHOLE flow (every
    real touchpoint) addressing it - the flow-level counterpart to
    run_insight_email_pipeline(). Same flow-picking logic. When nothing in
    the real catalog genuinely fits, the Head of CRM designs a new flow
    from the real signal instead of stopping to ask a human which existing
    one to force it into (synthesize_flow_for_signal()) - only falls back
    to needs_flow_clarification if that design call itself fails."""
    brief = investigate(question, file_context=file_context)

    if flow_name and not flow_genuinely_fits(question, flow_name):
        # An explicitly-named flow deserves the same "does this actually
        # fit" scrutiny as a freshly-picked one - never trusted just
        # because the message happened to mention its name (see
        # flow_genuinely_fits()'s own docstring for the real bug class
        # this guards against).
        flow_name = None
    if not flow_name:
        flow_name = pick_flow_for_signal(question, brief["brief_text"])
    if not flow_name:
        flow_name = synthesize_flow_for_signal(question, brief["brief_text"], raw_request=raw_request)
    if not flow_name:
        return {
            "needs_flow_clarification": True,
            "brief": brief,
            "question": question,
        }

    result = run_flow_pipeline(
        flow_name, insight_brief_text=brief["brief_text"], raw_request=raw_request or question, category=category,
        template_reference=template_reference,
    )
    result["needs_flow_clarification"] = False
    result["insight_brief"] = brief
    result["signal_question"] = question
    return result


def run_personalized_email_pipeline(
    identifier: str, first_name: str, flow_name: Optional[str] = None, notes: str = "",
    category: str = DEFAULT_CATEGORY,
) -> dict:
    """A SINGLE email personalized to one real, specific patient's real
    situation - looked up via investigate_patient() (the shared analytics
    agent, identity-scrubbed - see that function's own docstring for the
    real, honest limits of that scrubbing). If flow_name isn't given, the
    real flow is picked from the patient's own real situation, same
    reasoning as run_insight_email_pipeline picks one from a business
    signal. The patient lookup result is always returned, even when it
    found nothing or was blocked - never silently substituted with a
    fabricated finding."""
    patient_brief = investigate_patient(identifier, notes=notes)

    if not flow_name:
        flow_name = pick_flow_for_signal(patient_brief["brief_text"], patient_brief["brief_text"])
    if not flow_name:
        return {"needs_flow_clarification": True, "patient_brief": patient_brief, "identifier": identifier, "first_name": first_name}

    result = _run_pipeline_loop(flow_name, first_name, insight_brief=patient_brief["brief_text"], category=category)
    result["needs_flow_clarification"] = False
    result["patient_brief"] = patient_brief
    result["patient_identifier"] = identifier
    return result


def run_personalized_flow_pipeline(
    identifier: str, flow_name: Optional[str] = None, notes: str = "", raw_request: Optional[str] = None,
    category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None,
) -> dict:
    """The WHOLE-FLOW version of run_personalized_email_pipeline above -
    every touchpoint in the real cadence, grounded in one real patient's
    real situation instead of a business-wide signal. Reuses
    run_flow_pipeline exactly as run_insight_flow_pipeline does; only the
    source of insight_brief_text differs."""
    patient_brief = investigate_patient(identifier, notes=notes)

    if flow_name and not flow_genuinely_fits(patient_brief["brief_text"], flow_name):
        flow_name = None
    if not flow_name:
        flow_name = pick_flow_for_signal(patient_brief["brief_text"], patient_brief["brief_text"])
    if not flow_name:
        flow_name = synthesize_flow_for_signal(patient_brief["brief_text"], patient_brief["brief_text"], raw_request=raw_request)
    if not flow_name:
        return {"needs_flow_clarification": True, "patient_brief": patient_brief, "identifier": identifier}

    result = run_flow_pipeline(
        flow_name, insight_brief_text=patient_brief["brief_text"], raw_request=raw_request,
        category=category, template_reference=template_reference,
    )
    result["needs_flow_clarification"] = False
    result["patient_brief"] = patient_brief
    result["patient_identifier"] = identifier
    return result


def resolve_flow_for_request(request_text: str, candidate_flow_name: Optional[str] = None) -> Optional[str]:
    """Real, live-caught bug this fixes: a plain 'direct' build request
    describing a genuinely NEW flow ("signed up, never transacted" -
    materially different from any real catalog entry) got force-matched
    onto the closest-SOUNDING existing one (p1_plan_not_purchased) by
    parse_email_request's own first-pass classification, because that
    classifier's flow_name field can only ever return an existing catalog
    slug or null - there was no way for a request describing something
    new to say so. This applies the exact same "does this genuinely fit,
    or design something new" discipline run_insight_flow_pipeline already
    has for an investigated business signal, to a plain direct request:
    - `candidate_flow_name` (parse_email_request's own guess, if any) is
      verified via flow_genuinely_fits() before being trusted at all.
    - If that fails, or nothing was guessed, pick_flow_for_signal() gets
      an independent, full-catalog look at the request's own text.
    - Only if NOTHING in the real catalog genuinely fits does the Head of
      CRM design a brand new flow from the request itself
      (synthesize_flow_for_signal()) - matching a request as detailed as
      Thalia's own "SIGN UP NOT TRANSACTED" ask deserves a new flow, not
      a dead-end clarifying question, when nothing real actually applies.
    Returns None only if every one of those genuinely fails - the
    caller's own "which flow" question is the last resort, not the
    first."""
    if candidate_flow_name and flow_genuinely_fits(request_text, candidate_flow_name):
        return candidate_flow_name
    picked = pick_flow_for_signal(
        request_text, "(a direct build request describing exactly what's wanted, not an investigated live-data signal)",
    )
    # Verified again, not just trusted as the "stricter" second opinion -
    # live-caught in testing: pick_flow_for_signal's own re-pick can STILL
    # force-match a genuinely new request onto the closest existing flow
    # (a "signed up, never transacted" request landed on quiz_recovery -
    # incomplete QUIZ specifically, a different real audience) despite its
    # own "don't force a weak match" instruction. The same fit check is
    # the only thing that actually catches that, at either stage.
    if picked and flow_genuinely_fits(request_text, picked):
        return picked
    return synthesize_flow_for_signal(request_text, request_text, raw_request=request_text)


class _LiveDataRequest(BaseModel):
    wants_live_data: bool = Field(
        description="True only if this feedback explicitly asks to check/use REAL, LIVE business data "
        "(BigQuery, MoEngage, 'the database', 'live data', 'actual numbers') to inform this revision - "
        "not just 'make it more convincing/compelling' on its own, which is a general creative ask with "
        "no real data source implied."
    )
    query_question: Optional[str] = Field(
        default=None,
        description="If wants_live_data: the actual business question to investigate, as one clean "
        "sentence a data analyst could act on, grounded in this flow's real context (e.g. 'How many "
        "andSons customers have started treatment in total?'). Null otherwise.",
    )


def _resolve_live_data_request(feedback: str, flow_name: str) -> Optional[str]:
    """Determines whether a piece of human revision feedback is genuinely
    asking for a real, live database lookup (BigQuery/MoEngage) - a real
    reasoning call, same principle as resolve_touchpoint_reference(), not
    keyword matching. Returns the question to actually investigate, or
    None. Fails closed (None) on any error - a failed classification just
    means no live lookup happens, same as before this existed - the
    revision still proceeds normally without one."""
    llm = get_llm("HEAD_OF_CRM", temperature=0.0)  # a classification task, not creative writing
    structured_llm = llm.with_structured_output(_LiveDataRequest)
    system_text = (
        f"This is real human revision feedback on the andSons '{flow_name}' CRM flow. Determine whether "
        "it genuinely asks for real, live business data to be checked and used for this revision, or is "
        "just a general creative request with no real data source implied."
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
    if result.query_question:
        return result.query_question
    # Groq sometimes sets the boolean correctly but drops the dependent
    # field on the same call (same tool-calling quirk seen elsewhere in
    # this codebase) - a correctly-detected "yes they want live data" must
    # not get silently discarded just because the follow-up field came back
    # empty. Fall back to a concrete, answerable question rather than a
    # vague one an analytics query can't actually resolve (a generic "what
    # data exists" question reliably comes back empty-handed).
    return (
        "How many andSons customers have completed a consultation or started treatment in total? "
        "What other real aggregate customer numbers exist (signups, bookings, completions) that could "
        f"work as honest social proof for the '{flow_name}' flow? Context for why this is being asked: {feedback}"
    )


class _ImageChangeRequest(BaseModel):
    wants_image_change: bool = Field(
        description="True only if this feedback explicitly asks to change the PHOTO/IMAGE/HERO itself - a "
        "different picture, a different subject, removing or adding a hero image, swapping which bank photo "
        "is used. False for absolutely everything else, including wording/tone/CTA/price/structure changes "
        "that don't mention the photo at all - the image must never change as a side effect of some other "
        "edit, only when it's genuinely what was asked."
    )


def _feedback_asks_for_image_change(feedback: str) -> bool:
    """Determines whether a piece of revision feedback explicitly asks to
    change the hero photo - real reasoning, same principle as
    _resolve_live_data_request() above. Fails closed to False on any
    error or ambiguity: a real, reported problem this exists to fix is the
    hero photo changing on its own initiative on a revision round that
    never asked for that, so the safe default on an uncertain
    classification is to leave the image exactly as it is, never to change
    it speculatively."""
    llm = get_llm("HEAD_OF_CRM", temperature=0.0)  # a classification task, not creative writing
    structured_llm = llm.with_structured_output(_ImageChangeRequest)
    system_text = (
        "This is real human revision feedback on an andSons email that already has a real hero photo. "
        "Determine whether it explicitly asks to change that photo/image, as opposed to any other kind of "
        "edit (wording, tone, CTA, price, structure) that doesn't mention the image at all."
    )
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
    """Actually calls investigate() (real BigQuery, optionally real
    MoEngage) and turns the result into a plain-text section for
    format_human_feedback() - the real fix for the Copywriter inventing a
    plausible-sounding excuse for why it 'couldn't retrieve' live data it
    never actually had a way to try to retrieve. Now it genuinely tries,
    and the outcome (real finding, or a real 'nothing usable came back')
    is what gets handed to the Copywriter - never fabricated either way."""
    try:
        insight = investigate(query_question, file_context="")
    except Exception:
        logger.exception("Live data lookup failed for revision question %r", query_question[:120])
        return (
            "A live data lookup was just attempted for this request but failed technically (a real "
            "error, not a missing capability). Do not invent a number or claim you found one - if the "
            "feedback specifically required real data, say plainly in the note field that the lookup "
            "failed, and proceed without fabricating a substitute."
        )
    if insight.get("bigquery_verified"):
        return (
            "A LIVE DATA LOOKUP WAS JUST RUN FOR THIS REQUEST (real, verified BigQuery result):\n"
            f"{insight['bigquery_answer']}\n"
            "Use this genuinely if it strengthens the message - as strategic context/angle by default, "
            "or as a literal number in the copy ONLY if the feedback explicitly asked for a number to "
            "appear AND this one is customer-safe (a real aggregate/social-proof count, e.g. total "
            "customers - never an internal engagement/marketing metric, which must never appear in "
            "customer copy regardless of what was asked). Never invent a different number than this one."
        )
    return (
        "A live data lookup was just attempted for this request but did not return anything "
        "verified/usable. Do not fabricate a number or claim you found one - if the feedback "
        "specifically required real data, say plainly in the note field that the lookup ran but "
        "returned nothing usable, and proceed without inventing a substitute."
    )


def format_human_feedback(
    feedback: str,
    previous_draft: Optional[str] = None,
    feedback_history: Optional[list] = None,
    live_data_context: Optional[str] = None,
) -> str:
    """Turn a human reviewer's free-text note into a structured correction
    instruction. Same rule as format_correction(): never passed to the
    Copywriter alone — always appended to the full original constraints by
    generate_email().

    Grounds the model in the ACTUAL current draft text (so this is an edit
    task, not a blind rewrite) plus every prior feedback round in order (so
    a new note can't accidentally undo an earlier one)."""
    sections = []

    if previous_draft:
        sections.append(
            "Here is the CURRENT draft, exactly as it stands right now. Treat it as your starting "
            "point — you are editing this specific text, not writing a new email from a blank page:\n"
            "---\n" + previous_draft.strip() + "\n---"
        )

    if live_data_context:
        sections.append(live_data_context)

    if feedback_history:
        numbered = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(feedback_history))
        sections.append(
            "This draft already reflects ALL of this earlier feedback, applied in order. Every one of "
            "these must still hold true in your new draft — do not silently undo, soften, or drop any "
            "of them while addressing the new feedback below:\n" + numbered
        )

    sections.append(
        "NEW feedback to apply now, on top of everything above:\n"
        f"{feedback.strip()}\n\n"
        "Implement this new feedback exactly and completely — every specific REQUIREMENT in it must be "
        "reflected in the new draft (a real fact, number, or detail the feedback asks you to add or "
        "change must actually appear; don't approximate it, soften it, or address only part of it). If "
        "the feedback is about how something LOOKS or is LAID OUT rather than what it says, check whether "
        "this schema has a dedicated field for that exact thing (e.g. where the CTA button sits, what "
        "shape the what_happens_next markers are, the email's background colour) and set that field "
        "directly to your own genuine read of what they asked for — don't just describe the visual change "
        "in a sentence and leave the field untouched, and don't invent a workaround in the text if a real "
        "field for it already exists.\n\n"
        "HARD RULE if the feedback includes quoted example wording (\"something like 'X'\", \"e.g. 'X'\"): "
        "that quoted text is a sketch of the KIND of line wanted, not a script — you are BANNED from "
        "reusing more than two or three consecutive words from inside those quotes anywhere in your "
        "output. Pull out only the real substance (a number, a fact) and write an entirely new sentence "
        "around it, in this email's own voice, that a person could not tell was built from that example. "
        "Unrelated worked example of this exact rule, so you don't just copy this one either — feedback "
        "says 'mention the weather, something like \"it's a beautiful sunny day outside\"': reusing that "
        "clause verbatim (even reordered, even with one word swapped) is a FAIL; 'clear skies all "
        "afternoon' or 'not a cloud around today' both PASS — same idea, genuinely different sentence "
        "built from scratch, not edited from the quote.\n\n"
        "Everything in the current draft that the feedback doesn't ask you to change should stay the "
        "same. Don't introduce unrelated changes beyond what's needed to satisfy this feedback.\n\n"
        "This feedback overrides prior creative choices but never overrides compliance, the language "
        "rules, the flow's exact CTA label, or invent-nothing. If the feedback genuinely asks for "
        "something one of those hard rules doesn't allow, don't just silently refuse and don't silently "
        "reinterpret it into something smaller without saying so either — apply the closest compliant "
        "interpretation of what they actually asked for, and set the note field to one short, plain "
        "sentence naming the conflict: what they asked for, and why you couldn't do it literally.\n\n"
        "The note field is ONLY for THIS new feedback genuinely hitting one of the hard rules above "
        "(compliance, language rules, the flow's exact CTA label, invent-nothing) — never about an earlier "
        "round in the history above (those were already resolved when they happened; don't re-litigate or "
        "re-explain them here), and never, ever because a change simply has no dedicated field of its own: "
        "an aesthetic or content request is not a hard-rule conflict, it's a real, buildable change — a "
        "field for it either already exists (check the schema again before concluding otherwise) or the "
        "change belongs directly in an existing text/content field. Writing a note that says 'I can't do "
        "this, there's no field for it' is always wrong; either make the change through a real field, or if "
        "one genuinely doesn't cover it, make the closest real content edit that honestly reflects what was "
        "asked rather than refusing. Leave note null unless THIS feedback itself hits a real hard-rule "
        "conflict right now."
    )

    return "\n\n".join(sections)


def revise_with_feedback(
    flow_name: str,
    first_name: str,
    feedback: str,
    previous_rendered_text: Optional[str] = None,
    feedback_history: Optional[list] = None,
    category: str = DEFAULT_CATEGORY,
    previous_content: Optional[dict] = None,
    template_reference: Optional[str] = None,
) -> dict:
    """Human-in-the-loop revision. Re-invokes the Copywriter with the ORIGINAL
    system prompt/constraints plus the current draft, the full prior feedback
    thread, and the new feedback (never the new feedback alone) — same
    principle as the automatic Sweeper feedback loop, but driven by a person
    instead of the Sweeper, and with memory of everything asked for so far.

    Retries up to MAX_RETRIES when the Sweeper fails the result, same
    correction-loop principle as revise_flow_touchpoint()/run_flow_pipeline -
    this used to ship a Sweeper-failing draft after exactly one attempt,
    which meant a real, catchable defect went straight to a human instead of
    a normal automatic retry fixing it first.

    `previous_content`, when the caller has it (the main Slack path's
    revise_flow_touchpoint() always does; this function's own HTTP caller,
    /revise-email, currently only sends previous_rendered_text - a plain
    string with no structured hero field to lock from - so this stays a
    no-op there until that endpoint's contract is extended), locks the
    hero photo exactly the same way revise_flow_touchpoint() does - see
    _apply_hero_lock(). `template_reference` (an uploaded reference image
    for this revision) is a real design instruction, see
    copywriter_agent._TEMPLATE_REFERENCE_INSTRUCTION - also disables the
    hero lock above, same reasoning as revise_flow_touchpoint()."""
    feedback_history = feedback_history or []

    query_question = _resolve_live_data_request(feedback, flow_name)
    live_data_context = _run_live_data_lookup(query_question) if query_question else None

    locked_hero = None
    if previous_content and previous_content.get("hero", "none") != "none" and not template_reference:
        if not _feedback_asks_for_image_change(feedback):
            locked_hero = {"hero": previous_content["hero"], "hero_headline": previous_content.get("hero_headline")}

    previous_draft = previous_rendered_text
    sweeper_correction = None
    email = None
    sweep = None

    for attempt_num in range(MAX_RETRIES + 1):  # attempt 0 = the human's ask, 1 and 2 = Sweeper-driven retries
        correction = format_human_feedback(
            feedback, previous_draft=previous_draft, feedback_history=feedback_history,
            live_data_context=live_data_context,
        )
        if sweeper_correction:
            correction = correction + "\n\n" + sweeper_correction

        email = generate_email(flow_name, first_name, correction=correction, category=category, locked_hero=locked_hero, template_reference=template_reference)
        rendered = email["rendered_text"]
        sweep = sweep_email(rendered, flow_name=flow_name, hero_info=email["content"], human_feedback=feedback, category=category)
        logger.info(
            "Human feedback applied (flow=%s, round=%d, attempt=%d): %r | sweeper_pass=%s reasons=%s",
            flow_name,
            len(feedback_history) + 1,
            attempt_num + 1,
            feedback,
            sweep["pass"],
            sweep["reasons"],
        )

        if sweep["pass"] or attempt_num == MAX_RETRIES:
            break

        previous_draft = rendered
        sweeper_correction = format_correction(sweep["reasons"])

    rendered = email["rendered_text"]

    return {
        "email": email["content"],
        "rendered_text": rendered,
        "flow_name": flow_name,
        "first_name": first_name,
        "feedback": feedback,
        "feedback_history": feedback_history + [feedback],
        "sweeper_pass": sweep["pass"],
        "sweeper_reasons": sweep["reasons"],
        "sweeper_severity": sweep["severity"],
    }


class _TouchpointReference(BaseModel):
    touchpoint_ns: List[int] = Field(
        default_factory=list,
        description="EVERY real step number this feedback should be applied to, confidently resolved from "
        "what it actually says and each step's real content below. Put exactly one number here if it's "
        "about a single step (e.g. 'move the button to the right' matches whichever step's real CTA was "
        "mentioned, or the only plausible match). Put SEVERAL numbers here - not in candidate_ns below - "
        "when the feedback EXPLICITLY asks for the same change across more than one step, however it's "
        "phrased ('in all three steps', 'steps 1, 2 and 3', 'on every step', 'both emails'): that is a "
        "confident, unambiguous multi-step instruction, not a case that needs asking which one - resolve "
        "it to every real step number it names or clearly means. Leave empty only if it doesn't confidently "
        "resolve to any step(s) at all.",
    )
    candidate_ns: List[int] = Field(
        default_factory=list,
        description="ONLY if touchpoint_ns is empty because the feedback is genuinely AMBIGUOUS - it "
        "matches two or more touchpoints about equally well (e.g. several share the same CTA text and "
        "nothing else narrows it down) AND it does NOT explicitly ask for all/several of them (that case "
        "belongs in touchpoint_ns above, not here): list every step number that could plausibly be meant, "
        "so the person can be asked a specific question naming just those steps. Leave empty if "
        "touchpoint_ns was resolved, or if the feedback matches nothing at all.",
    )
    reason: str = Field(description="One short line explaining the resolution (or why it's ambiguous/no match).")


def _touchpoint_content_summary(t: dict) -> str:
    content = t.get("content")
    if content is None:
        return "(failed to generate - nothing to reference)"
    if t["channel"] == "email":
        position = content.get("cta_position", 0.0) or 0.0
        bg = content.get("background_color") or "the standard brand default"
        return (
            f"subject {content['subject']!r}, hero {content.get('hero')}, has a real REPOSITIONABLE CTA "
            f"BUTTON reading {content['cta_text']!r} currently sitting at horizontal position "
            f"{position:.2f} (0.0=flush left, 0.5=centre, 1.0=flush right), background colour currently "
            f"{bg} - the only channel where repositioning 'the button' or changing the page background is "
            f"even possible"
        )
    if t["channel"] == "whatsapp":
        return (
            f"opens {content['hook_line']!r}, CTA reading {content['cta_text']!r} rendered as a plain "
            f"green underlined text link"
        )
    return (
        f"title {content['title']!r}, body {content['body']!r} - a phone notification with NO button or "
        f"link of any kind, tap-to-open only"
    )  # push


def resolve_touchpoint_reference(feedback_text: str, touchpoints: list) -> dict:
    """A reply in a flow thread that doesn't start with an explicit step
    number (e.g. 'move the button to the right') shouldn't just bounce
    back asking for one when the actual touchpoint content already makes
    it obvious - matches feedback against what each touchpoint's real
    content actually says, the same 'resolve from context before asking'
    principle as analytics_agent._resolve_followup_question(). Also tells
    apart a genuinely AMBIGUOUS reference (real bug caught live: 'in all
    three steps, move the button to the middle' - and every rephrasing of
    it - kept getting bounced back asking the person to pick just one,
    because the old shape had no way to say 'this confidently means
    several real steps at once', only 'pick one' or 'ask a human to
    disambiguate' - an explicit multi-step instruction is neither of
    those). Fails open to no-match (touchpoint_ns=[], candidate_ns=[]) on
    any error, so the caller's existing generic clarifying question still
    works as a fallback rather than the whole reply silently failing."""
    lines = [f"- Step {t['n']} ({t['channel']}, {t['timing']}): {_touchpoint_content_summary(t)}" for t in touchpoints]
    llm = get_llm("HEAD_OF_CRM", temperature=0.0)  # a resolution/classification task, not creative writing
    structured_llm = llm.with_structured_output(_TouchpointReference)
    system_text = (
        "Determine which of these real touchpoints a reviewer's feedback is about, using only their "
        "actual content below:\n" + "\n".join(lines)
    )
    # ChatPromptTemplate scans message strings for "{var}" patterns even
    # when they were already fully built via an f-string - feedback_text
    # is raw Slack user input, so a literal brace typed by a real person
    # would otherwise crash this exact call the same way an unfilled
    # template variable just did.
    escaped_feedback = feedback_text.replace("{", "{{").replace("}", "}}")
    # Real bug caught live: phrasing this as a literal question ("Which
    # step is this about?") made the model answer it conversationally in
    # plain text instead of calling the structured-output tool - same
    # failure mode as the Head of CRM prompt fix (see that agent's
    # module docstring). An instruction, not a question, fixed it there;
    # same fix here.
    human_text = f"Reviewer feedback: {escaped_feedback}"
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm
    result, last_exc = invoke_with_retry(chain, label="Touchpoint reference resolution call")
    if result is None:
        logger.warning("Touchpoint reference resolution failed (%s) - falling back to asking directly.", last_exc)
        return {"touchpoint_ns": [], "candidate_ns": [], "reason": "resolution unavailable"}
    return result.model_dump()


def revise_flow_touchpoint(
    flow_name: str,
    touchpoints: list,
    touchpoint_n: int,
    feedback: str,
    feedback_history: Optional[list] = None,
    category: str = DEFAULT_CATEGORY,
    template_reference: Optional[str] = None,
) -> dict:
    """Human-in-the-loop revision of ONE touchpoint in an already-generated
    flow - same principle as revise_with_feedback(), but aware of its real
    position in the sequence (touchpoints before it are passed as context,
    same as during generation, so a revision can't accidentally contradict
    what an earlier touchpoint already said).

    Retries up to MAX_RETRIES when the Sweeper fails the result (same
    correction-loop principle as run_flow_pipeline/_run_pipeline_loop) -
    this used to be a single shot that shipped a Sweeper-failing draft
    straight to Slack labelled "needs review" even when a normal automatic
    retry would have fixed it. Each retry keeps the human's original
    feedback in force (grounded on the draft that just came out of the
    previous attempt) while also handing the Copywriter the Sweeper's exact
    reasons to fix, same wording generate_flow's own retry loop uses.
    `template_reference` (from an uploaded reference image for this
    revision, see agents.template_agent) is a real design instruction -
    see copywriter_agent._TEMPLATE_REFERENCE_INSTRUCTION."""
    feedback_history = feedback_history or []
    target = next((t for t in touchpoints if t["n"] == touchpoint_n), None)
    if target is None:
        raise ValueError(f"No touchpoint {touchpoint_n} in this flow (has {[t['n'] for t in touchpoints]}).")

    prior_summaries = [_touchpoint_summary(t) for t in touchpoints if t["n"] < touchpoint_n]
    step = {"n": target["n"], "channel": target["channel"], "timing": target["timing"], "intent": target["intent"]}
    other_heroes = _heroes_from_touchpoints(touchpoints, touchpoint_n)

    # Real, reported problem this fixes: the hero photo changing on its own
    # initiative on a revision round that never asked for a different
    # image. Locked by default for an email touchpoint that already has a
    # real hero - unlocked when this specific feedback genuinely asks to
    # change the photo (see _apply_hero_lock/_feedback_asks_for_image_change),
    # or when a template reference image was uploaded for this round (its
    # whole point can be reconsidering the look, hero included).
    locked_hero = None
    target_content = target.get("content") or {}
    if target["channel"] == "email" and target_content.get("hero", "none") != "none" and not template_reference:
        if not _feedback_asks_for_image_change(feedback):
            locked_hero = {"hero": target_content["hero"], "hero_headline": target_content.get("hero_headline")}

    # Run the live-data lookup (if this feedback genuinely asks for one) ONCE,
    # not per-retry - a real BigQuery/MoEngage query, same one every retry
    # attempt reuses, so a flaky reclassification can't silently query twice
    # with different answers within a single revision.
    query_question = _resolve_live_data_request(feedback, flow_name)
    live_data_context = _run_live_data_lookup(query_question) if query_question else None

    previous_draft = target["rendered_text"]
    sweeper_correction = None
    new_touchpoint = None
    sweep = None

    for attempt_num in range(MAX_RETRIES + 1):  # attempt 0 = the human's ask, 1 and 2 = Sweeper-driven retries
        correction = format_human_feedback(
            feedback, previous_draft=previous_draft, feedback_history=feedback_history,
            live_data_context=live_data_context,
        )
        if sweeper_correction:
            correction = correction + "\n\n" + sweeper_correction

        try:
            new_touchpoint = generate_touchpoint(flow_name, step, prior_summaries, correction=correction, category=category, locked_hero=locked_hero, template_reference=template_reference)
            sweep = _sweep_touchpoint(new_touchpoint, flow_name, other_heroes=other_heroes, human_feedback=feedback, category=category)
        except RuntimeError as exc:
            # Same real, if rare, exhausted-retry failure as generate_flow() -
            # a manual retry request itself failing must never crash back to a
            # raw error either; leaves the touchpoint retryable again.
            logger.error("Retry of touchpoint %d failed to generate: %s", touchpoint_n, exc)
            new_touchpoint = {
                "n": step["n"], "channel": step["channel"], "timing": step["timing"], "intent": step["intent"],
                "content": None, "rendered_text": None, "hero": None, "generation_failed": True,
                "passed": False,
                "sweeper_reasons": ["Content generation failed again - try replying with different wording, or try again in a moment."],
                "sweeper_severity": "major",
            }
            updated_touchpoints = [new_touchpoint if t["n"] == touchpoint_n else t for t in touchpoints]
            return {
                "flow_name": flow_name,
                "touchpoint": new_touchpoint,
                "touchpoints": updated_touchpoints,
                "feedback_history": feedback_history + [f"[step {touchpoint_n}] {feedback}"],
            }

        logger.info(
            "Human feedback applied to flow=%s touchpoint=%d attempt=%d: %r | sweeper_pass=%s reasons=%s",
            flow_name, touchpoint_n, attempt_num + 1, feedback, sweep["pass"], sweep["reasons"],
        )

        if sweep["pass"] or attempt_num == MAX_RETRIES:
            break

        previous_draft = new_touchpoint["rendered_text"]
        sweeper_correction = format_correction(sweep["reasons"])

    new_touchpoint["passed"] = sweep["pass"]
    new_touchpoint["sweeper_reasons"] = sweep["reasons"]
    new_touchpoint["sweeper_severity"] = sweep["severity"]

    # Skill Distiller: a real human correction just got successfully
    # applied - turn it into a standing rule so the NEXT flow (any flow,
    # not just this one) doesn't need the same correction given again.
    # Best-effort, never blocks the response on it.
    if sweep["pass"]:
        distill_and_save_rule(feedback, new_touchpoint["rendered_text"] or "")

    updated_touchpoints = [new_touchpoint if t["n"] == touchpoint_n else t for t in touchpoints]
    return {
        "flow_name": flow_name,
        "touchpoint": new_touchpoint,
        "touchpoints": updated_touchpoints,
        "feedback_history": feedback_history + [f"[step {touchpoint_n}] {feedback}"],
    }


def revise_flow_touchpoints(
    flow_name: str,
    touchpoints: list,
    touchpoint_ns: List[int],
    feedback: str,
    feedback_history: Optional[list] = None,
    category: str = DEFAULT_CATEGORY,
    template_reference: Optional[str] = None,
) -> dict:
    """Human-in-the-loop revision of MULTIPLE existing touchpoints with the
    SAME feedback in one go - what resolve_touchpoint_reference()'s
    touchpoint_ns resolves to when a reply explicitly names or means
    several real steps ('in all three steps...', 'steps 1, 2 and 3...').
    Applies revise_flow_touchpoint() to each real step in turn, threading
    the growing touchpoints list and feedback_history through so each
    step's revision runs against the previous step's already-updated state
    (matters for anything that reads sibling touchpoints, e.g. hero
    uniqueness) rather than N independent calls against a stale snapshot.
    Every step still gets its own full Sweeper retry loop - one step
    failing never blocks or reverts another's."""
    feedback_history = feedback_history or []
    current_touchpoints = touchpoints
    revised = []
    for n in touchpoint_ns:
        step_result = revise_flow_touchpoint(
            flow_name, current_touchpoints, n, feedback, feedback_history=feedback_history, category=category,
            template_reference=template_reference,
        )
        current_touchpoints = step_result["touchpoints"]
        feedback_history = step_result["feedback_history"]
        revised.append(step_result["touchpoint"])
    return {
        "flow_name": flow_name,
        "revised": revised,
        "touchpoints": current_touchpoints,
        "feedback_history": feedback_history,
    }


# --- Structural feedback: changing HOW MANY steps a flow has, not just one
# step's content. This is the reasoning counterpart to the Head of CRM's
# cadence decision at build time (head_of_crm_agent.py) - that call decides
# the touchpoint plan ONCE, up front; this is the same kind of judgement
# call applied to a flow that's already been generated and is now getting
# human feedback that asks to change its SHAPE, not just its wording. No
# fixed list of trigger phrases - the model reads the real feedback against
# the real current touchpoints and decides, same principle as
# resolve_touchpoint_reference() and _resolve_live_data_request() above.


class _TargetStep(BaseModel):
    n: int = Field(description="This step's position in the NEW sequence, starting at 1, contiguous, no gaps.")
    channel: Literal["email", "whatsapp"] = Field(
        description="The real channel for this step. andSons only ever sends on two real channels, email "
        "and whatsapp - never propose anything else, there is no push/notification/SMS channel."
    )
    timing: str = Field(description="When this step fires, relative to the trigger or its neighbours.")
    intent: str = Field(description="One line: what this step is for - distinct from every other step's.")
    from_existing_n: Optional[int] = Field(
        default=None,
        description="If this step's real content should carry over UNCHANGED from the CURRENT flow (same "
        "message, just possibly at a new position/number) - the CURRENT step number it comes from. Only "
        "valid when this step's `channel` above is the SAME real channel that current step already has - "
        "email content and whatsapp content are structurally different, so a channel conversion always "
        "means fresh content, never a carry-over, even if nothing else about the step's purpose changed. "
        "Null if this step is brand new, its channel just changed, or the feedback specifically asked to "
        "change what it says. Reuse existing content whenever the feedback gives no real reason to touch it "
        "- never regenerate a step nobody asked about just because the flow around it changed shape."
    )


class _StructuralRequest(BaseModel):
    changed: bool = Field(
        description="Whether this feedback asks to change the flow's real SHAPE at all - add a step, "
        "remove one, reorder, convert an existing step's channel, or reach any stated structural/"
        "composition target (e.g. 'I want 2 emails in this flow', 'make it 3 steps'). False if it's just a "
        "normal content edit to one existing step's own wording/tone/image/price/fact, where every step and "
        "its channel stays exactly where it is - that case falls through to the regular per-step content-"
        "edit path instead, so target_cadence should be left null."
    )
    target_cadence: Optional[List[_TargetStep]] = Field(
        default=None,
        description="Only when changed=True: the flow's COMPLETE real touchpoint sequence as it should be "
        "AFTER applying this feedback - not a diff, not one operation, the whole thing, reasoned freely "
        "from everything you've been given (the flow's current real steps, and the FULL feedback history of "
        "this thread so far, not just the latest message - a later message can refer back to something "
        "asked earlier, e.g. 'I said I want 2 emails', and you have everything you need above to resolve "
        "that against what's actually true right now). Handle any combination of add/remove/reorder/convert "
        "in one pass; there's no fixed catalog of allowed operation types, just reason to the correct real "
        "end state. Null when changed=False."
    )
    reasoning: str = Field(description="One short line explaining the resolution (or why changed=False).")


def _resolve_structural_request(feedback_text: str, touchpoints: list, flow_name: str, feedback_history: Optional[list] = None) -> dict:
    """Determines whether a reply in an already-generated flow's thread is
    asking to change the flow's real SHAPE, and if so, what the flow's
    complete real touchpoint sequence should look like afterwards - one
    holistic reasoning call against the flow's actual current touchpoints
    AND the full feedback history of this thread so far (not just the
    latest message in isolation), same principle as
    resolve_touchpoint_reference() and _resolve_live_data_request(): no
    fixed catalog of named operations to special-case in code, the model
    reasons to the real target shape and code just diffs old vs. new to
    know what needs regenerating. Fails closed to changed=False on any
    error, so a classification failure just means this feedback falls
    through to the existing content-edit path exactly as it did before
    this existed - never a crash, never a silently-dropped request."""
    lines = [f"- Step {t['n']} ({t['channel']}, {t['timing']}): {_touchpoint_content_summary(t)}" for t in touchpoints]
    history_text = (
        "\n".join(f"- {h}" for h in feedback_history) if feedback_history
        else "(none yet - this is the first feedback on this flow)"
    )
    llm = get_llm("HEAD_OF_CRM", temperature=0.0)  # a reasoning/classification task, not creative writing
    structured_llm = llm.with_structured_output(_StructuralRequest)
    system_text = (
        f"This is real human feedback on the andSons '{flow_name}' flow, which currently has these real "
        "steps:\n" + "\n".join(lines) + "\n\nFULL FEEDBACK HISTORY ON THIS FLOW SO FAR, IN ORDER (what was "
        "asked, and what was actually done about it - a later message can reference this, e.g. 'I said...' "
        "or 'like I asked before'; use it to reason correctly rather than starting from scratch each time):\n"
        + history_text + "\n\nDetermine whether the NEW feedback below asks to change the flow's real shape, "
        "and if so, reason out its complete correct real touchpoint sequence afterwards."
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
        # Never trust step numbering blindly - a model tool-calling quirk
        # (documented on _resolve_live_data_request above) can produce
        # gaps/duplicates on an otherwise-good call. Renumber to a clean
        # 1..N sequence in whatever order it was returned rather than
        # reject a real, usable answer over a numbering slip.
        ordered = sorted(resolved["target_cadence"], key=lambda s: s["n"])
        for i, step in enumerate(ordered, start=1):
            step["n"] = i
        resolved["target_cadence"] = ordered
    return resolved


def _generate_and_sweep(
    flow_name: str, step: dict, prior_summaries: list, correction: str, other_heroes: list,
    human_feedback: Optional[str] = None, category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None,
):
    """Shared generate -> Sweeper -> retry loop for a touchpoint that has no
    PREVIOUS DRAFT of its own to ground a revision against (a brand-new
    step being added, not an edit to one that already exists) - same
    MAX_RETRIES correction-loop principle used everywhere else in this
    file, factored out here so apply_target_cadence() doesn't hand-roll a
    fourth copy of the same retry logic already written for
    _run_pipeline_loop/run_flow_pipeline/revise_flow_touchpoint. Returns
    (touchpoint, sweep_result)."""
    working_correction = correction
    touchpoint = None
    sweep = None
    for attempt_num in range(MAX_RETRIES + 1):
        try:
            touchpoint = generate_touchpoint(flow_name, step, prior_summaries, correction=working_correction, category=category, template_reference=template_reference)
        except RuntimeError as exc:
            logger.error("New touchpoint %d (%s) failed to generate: %s", step["n"], step["channel"], exc)
            touchpoint = {
                "n": step["n"], "channel": step["channel"], "timing": step["timing"], "intent": step["intent"],
                "content": None, "rendered_text": None, "hero": None, "generation_failed": True,
            }
            sweep = {
                "pass": False,
                "reasons": ["Content generation failed after retrying - reply again in a moment to retry this step."],
                "severity": "major",
            }
            break

        sweep = _sweep_touchpoint(touchpoint, flow_name, other_heroes=other_heroes, human_feedback=human_feedback, category=category)
        logger.info(
            "New touchpoint %d (%s) attempt %d: pass=%s severity=%s reasons=%s",
            step["n"], step["channel"], attempt_num + 1, sweep["pass"], sweep["severity"], sweep["reasons"],
        )
        if sweep["pass"] or attempt_num == MAX_RETRIES:
            break
        working_correction = correction + "\n\n" + format_correction(sweep["reasons"])

    return touchpoint, sweep


def apply_target_cadence(
    flow_name: str,
    touchpoints: list,
    target_cadence: list,
    feedback: str,
    feedback_history: Optional[list] = None,
    category: str = DEFAULT_CATEGORY,
    template_reference: Optional[str] = None,
) -> dict:
    """Human-in-the-loop RESHAPE of an already-generated flow to a new
    target sequence - the single, general counterpart to
    revise_flow_touchpoint() (which only ever edits one existing step's
    content in place) for feedback that changes the flow's real shape
    instead: adding, removing, reordering, or converting a step's channel,
    in any combination, in one pass. `target_cadence` is
    _resolve_structural_request()'s own real reasoning (given the flow's
    current steps AND the full feedback history of this thread) about what
    the complete sequence should be - this function does no shape
    reasoning of its own, it only diffs old vs. new and executes:

    - A target step with `from_existing_n` set carries its real content
      over UNCHANGED (only its n/timing/intent labels may move) - never
      regenerated, so a step nobody asked about never drifts.
    - A target step with no `from_existing_n` is generated fresh (the same
      generate_touchpoint() -> Sweeper retry loop every other touchpoint in
      this codebase goes through), with whatever real steps precede it IN
      THE NEW ORDER as its only prior context - a mix of carried-over and
      newly-written steps, exactly as the final flow will actually read.
    - Any current step whose number isn't reused by any target step is
      implicitly dropped."""
    feedback_history = feedback_history or []
    current_by_n = {t["n"]: t for t in touchpoints}
    ordered_target = sorted(target_cadence, key=lambda s: s["n"])

    # Pass 1: lay out the final sequence - carried-over steps get their
    # real content immediately, regenerated steps start as placeholders
    # (content=None) so pass 2 can compute other_heroes/prior context from
    # the real, final shape of the flow as it fills in, same convention
    # _heroes_from_touchpoints already relies on elsewhere in this file.
    new_touchpoints = []
    for target in ordered_target:
        source = current_by_n.get(target.get("from_existing_n")) if target.get("from_existing_n") else None
        # Defensive, not just a prompt instruction: email and whatsapp
        # content are structurally different shapes (hook_line/cta_text vs.
        # subject/hero/cta_position/...) - never honor a carry-over across
        # a channel change even if the model marked one, or the OLD
        # channel's content would silently ship unconverted.
        if source is not None and source.get("channel") != target["channel"]:
            source = None
        if source is not None:
            new_touchpoints.append({**source, "n": target["n"], "timing": target["timing"], "intent": target["intent"]})
        else:
            new_touchpoints.append({
                "n": target["n"], "channel": target["channel"], "timing": target["timing"],
                "intent": target["intent"], "content": None, "rendered_text": None, "hero": None,
            })

    # Pass 2: generate every placeholder, front to back, so each one's
    # prior context is the real (carried-over or already-regenerated)
    # content immediately before it.
    generated_ns = []
    for i, tp in enumerate(new_touchpoints):
        if tp.get("content") is not None or tp.get("generation_failed"):
            continue  # real carried-over content - never regenerated
        prior_summaries = [_touchpoint_summary(t) for t in new_touchpoints[:i] if t.get("content") is not None]
        step = {"n": tp["n"], "channel": tp["channel"], "timing": tp["timing"], "intent": tp["intent"]}
        other_heroes = _heroes_from_touchpoints(new_touchpoints, tp["n"])
        correction = (
            "This step is being written/rewritten because a real reviewer's feedback on this flow requires "
            f"it. Their actual request: {feedback.strip()}\n\n"
            "Write this step's content fully fresh for its real place in the sequence (see the prior-steps "
            "context you were given) - never reference its step number in the copy itself, just write for "
            "this specific moment in the customer's journey."
        )
        new_touchpoint, sweep = _generate_and_sweep(
            flow_name, step, prior_summaries, correction, other_heroes, human_feedback=feedback, category=category,
            template_reference=template_reference,
        )
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

    return {
        "flow_name": flow_name,
        "touchpoints": new_touchpoints,
        "feedback_history": feedback_history + [f"[restructured: {change_summary}] {feedback}"],
    }

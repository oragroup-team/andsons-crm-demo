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
from typing import Optional

from .copywriter_agent import (
    _touchpoint_summary,
    generate_email,
    generate_flow,
    generate_flow_email_touchpoint,
    generate_flow_whatsapp_touchpoint,
    pick_flow_for_signal,
)
from .insight_agent import investigate
from .sweeper_agent import sweep_email, sweep_whatsapp

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


def _run_pipeline_loop(flow_name: str, first_name: str, insight_brief: Optional[str] = None) -> dict:
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
        email = generate_email(flow_name, first_name, correction=correction, insight_brief=insight_brief)
        rendered = email["rendered_text"]

        sweep = sweep_email(rendered, flow_name=flow_name, hero_info=email["content"])

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


def run_email_pipeline(flow_name: str, first_name: str, file_context: str = "") -> dict:
    """Copywriter -> Sweeper -> Feedback loop, capped at MAX_RETRIES retries.
    file_context, if given (a summary from a file uploaded alongside the
    request), is passed through as strategy context via the same
    leak-prevention path as an insight brief - see generate_email()'s
    _INSIGHT_BRIEF_INSTRUCTION."""
    brief = f"DATA FROM A FILE UPLOADED WITH THIS REQUEST:\n{file_context}" if file_context else None
    return _run_pipeline_loop(flow_name, first_name, insight_brief=brief)


def run_insight_email_pipeline(
    question: str, first_name: str, flow_name: Optional[str] = None, file_context: str = ""
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

    result = _run_pipeline_loop(flow_name, first_name, insight_brief=brief["brief_text"])
    result["needs_flow_clarification"] = False
    result["insight_brief"] = brief
    result["signal_question"] = question
    return result


def run_flow_pipeline(flow_name: str, file_context: str = "", insight_brief_text: Optional[str] = None) -> dict:
    """Generate the WHOLE real flow - every Email + WhatsApp touchpoint in
    its real MoEngage cadence (flows.py) - not just one email. Each
    touchpoint goes through its own Sweeper QA gate; a touchpoint that
    fails is regenerated (capped at MAX_RETRIES) in place, using the same
    correction-loop principle as _run_pipeline_loop, without discarding or
    re-generating the touchpoints around it (regenerating the whole
    sequence over one failing WhatsApp line would also throw away good
    passing emails, and would risk small wording drift between runs)."""
    brief_parts = []
    if insight_brief_text:
        brief_parts.append(insight_brief_text)
    if file_context:
        brief_parts.append(f"DATA FROM A FILE UPLOADED WITH THIS REQUEST:\n{file_context}")
    brief = "\n\n".join(brief_parts) if brief_parts else None
    flow_result = generate_flow(flow_name, insight_brief=brief)

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
            if touchpoint["channel"] == "email":
                sweep = sweep_email(touchpoint["rendered_text"], flow_name=flow_name, hero_info=touchpoint["content"])
            else:
                sweep = sweep_whatsapp(touchpoint["rendered_text"], flow_name=flow_name)

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
                if touchpoint["channel"] == "email":
                    touchpoint = generate_flow_email_touchpoint(flow_name, step, prior_summaries, correction=correction, insight_brief=brief)
                else:
                    touchpoint = generate_flow_whatsapp_touchpoint(flow_name, step, prior_summaries, correction=correction, insight_brief=brief)
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
    }


def run_insight_flow_pipeline(
    question: str, flow_name: Optional[str] = None, file_context: str = ""
) -> dict:
    """Investigate a business signal, then generate the WHOLE flow (every
    real touchpoint) addressing it - the flow-level counterpart to
    run_insight_email_pipeline(). Same flow-picking logic; same
    needs_flow_clarification escape hatch when nothing genuinely fits."""
    brief = investigate(question, file_context=file_context)

    if not flow_name:
        flow_name = pick_flow_for_signal(question, brief["brief_text"])
    if not flow_name:
        return {
            "needs_flow_clarification": True,
            "brief": brief,
            "question": question,
        }

    result = run_flow_pipeline(flow_name, insight_brief_text=brief["brief_text"])
    result["needs_flow_clarification"] = False
    result["insight_brief"] = brief
    result["signal_question"] = question
    return result


def format_human_feedback(
    feedback: str,
    previous_draft: Optional[str] = None,
    feedback_history: Optional[list] = None,
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
        "Implement this new feedback exactly and completely — every specific instruction in it must be "
        "reflected in the new draft (a specific fact, phrase, name, or detail the feedback asks you to "
        "add or change must actually appear; don't approximate it, soften it, or address only part of "
        "it). Everything in the current draft that the feedback doesn't ask you to change should stay "
        "the same. Don't introduce unrelated changes beyond what's needed to satisfy this feedback."
    )

    return "\n\n".join(sections)


def revise_with_feedback(
    flow_name: str,
    first_name: str,
    feedback: str,
    previous_rendered_text: Optional[str] = None,
    feedback_history: Optional[list] = None,
) -> dict:
    """Human-in-the-loop revision. Re-invokes the Copywriter with the ORIGINAL
    system prompt/constraints plus the current draft, the full prior feedback
    thread, and the new feedback (never the new feedback alone) — same
    principle as the automatic Sweeper feedback loop, but driven by a person
    instead of the Sweeper, and with memory of everything asked for so far.
    This runs exactly one revision per call (not a retry loop): the human
    decides when to revise again. The Sweeper still checks the result once,
    purely for visibility."""
    feedback_history = feedback_history or []

    correction = format_human_feedback(
        feedback, previous_draft=previous_rendered_text, feedback_history=feedback_history
    )
    email = generate_email(flow_name, first_name, correction=correction)
    rendered = email["rendered_text"]

    sweep = sweep_email(rendered, flow_name=flow_name, hero_info=email["content"])
    logger.info(
        "Human feedback applied (flow=%s, round=%d): %r | sweeper_pass=%s reasons=%s",
        flow_name,
        len(feedback_history) + 1,
        feedback,
        sweep["pass"],
        sweep["reasons"],
    )

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


def revise_flow_touchpoint(
    flow_name: str,
    touchpoints: list,
    touchpoint_n: int,
    feedback: str,
    feedback_history: Optional[list] = None,
) -> dict:
    """Human-in-the-loop revision of ONE touchpoint in an already-generated
    flow - same principle as revise_with_feedback(), but aware of its real
    position in the sequence (touchpoints before it are passed as context,
    same as during generation, so a revision can't accidentally contradict
    what an earlier touchpoint already said)."""
    feedback_history = feedback_history or []
    target = next((t for t in touchpoints if t["n"] == touchpoint_n), None)
    if target is None:
        raise ValueError(f"No touchpoint {touchpoint_n} in this flow (has {[t['n'] for t in touchpoints]}).")

    prior_summaries = [_touchpoint_summary(t) for t in touchpoints if t["n"] < touchpoint_n]
    step = {"n": target["n"], "channel": target["channel"], "timing": target["timing"], "intent": target["intent"]}
    correction = format_human_feedback(
        feedback, previous_draft=target["rendered_text"], feedback_history=feedback_history
    )

    try:
        if target["channel"] == "email":
            new_touchpoint = generate_flow_email_touchpoint(flow_name, step, prior_summaries, correction=correction)
            sweep = sweep_email(new_touchpoint["rendered_text"], flow_name=flow_name, hero_info=new_touchpoint["content"])
        else:
            new_touchpoint = generate_flow_whatsapp_touchpoint(flow_name, step, prior_summaries, correction=correction)
            sweep = sweep_whatsapp(new_touchpoint["rendered_text"], flow_name=flow_name)
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

    new_touchpoint["passed"] = sweep["pass"]
    new_touchpoint["sweeper_reasons"] = sweep["reasons"]
    new_touchpoint["sweeper_severity"] = sweep["severity"]
    logger.info(
        "Human feedback applied to flow=%s touchpoint=%d: %r | sweeper_pass=%s reasons=%s",
        flow_name, touchpoint_n, feedback, sweep["pass"], sweep["reasons"],
    )

    updated_touchpoints = [new_touchpoint if t["n"] == touchpoint_n else t for t in touchpoints]
    return {
        "flow_name": flow_name,
        "touchpoint": new_touchpoint,
        "touchpoints": updated_touchpoints,
        "feedback_history": feedback_history + [f"[step {touchpoint_n}] {feedback}"],
    }

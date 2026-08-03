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

from .copywriter_agent import generate_email
from .sweeper_agent import sweep_email

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


def run_email_pipeline(flow_name: str, first_name: str) -> dict:
    """Copywriter -> Sweeper -> Feedback loop, capped at MAX_RETRIES retries."""
    attempts = []
    previous_text = None
    correction = None
    final_email = None
    final_sweep = None
    needs_human_review = False

    for attempt_num in range(MAX_RETRIES + 1):  # attempt 0 = first draft, 1 and 2 = retries
        email = generate_email(flow_name, first_name, correction=correction)
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

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
from typing import List, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from .copywriter_agent import (
    _touchpoint_summary,
    generate_email,
    generate_flow,
    generate_touchpoint,
    pick_flow_for_signal,
)
from .head_of_crm_agent import brief_campaign
from .insight_agent import investigate
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
    touchpoint: dict, flow_name: str, other_heroes: Optional[list] = None, human_feedback: Optional[str] = None
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
            other_heroes=other_heroes, human_feedback=human_feedback,
        )
    if touchpoint["channel"] == "whatsapp":
        return sweep_whatsapp(touchpoint["rendered_text"], flow_name=flow_name, human_feedback=human_feedback)
    if touchpoint["channel"] == "push":
        return sweep_push(touchpoint["rendered_text"], flow_name=flow_name, human_feedback=human_feedback)
    raise ValueError(f"Unknown channel: {touchpoint['channel']!r}")


def run_flow_pipeline(flow_name: str, file_context: str = "", insight_brief_text: Optional[str] = None) -> dict:
    """Generate the WHOLE real flow - every Email + WhatsApp touchpoint in
    its real MoEngage cadence (flows.py) - not just one email. Each
    touchpoint goes through its own Sweeper QA gate; a touchpoint that
    fails is regenerated (capped at MAX_RETRIES) in place, using the same
    correction-loop principle as _run_pipeline_loop, without discarding or
    re-generating the touchpoints around it (regenerating the whole
    sequence over one failing WhatsApp line would also throw away good
    passing emails, and would risk small wording drift between runs)."""
    # Head of CRM runs FIRST, unconditionally, on every flow build - not
    # only ones explicitly framed as a business signal (matching the real
    # pipeline: Head of CRM -> Copywriter is fixed, never skipped). It
    # turns whatever's known (the flow's real metadata, plus any live
    # signal that motivated this specific request) into one decisive
    # commercial brief the Copywriter executes against.
    crm_brief = brief_campaign(flow_name, signal_context=insight_brief_text, learned_rules=learned_rules_text())

    brief_parts = [f"CAMPAIGN BRIEF (Head of CRM):\n{crm_brief['brief_text']}"]
    if file_context:
        brief_parts.append(f"DATA FROM A FILE UPLOADED WITH THIS REQUEST:\n{file_context}")
    brief = "\n\n".join(brief_parts)
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
            other_heroes = _heroes_from_touchpoints(flow_result["touchpoints"], touchpoint["n"])
            sweep = _sweep_touchpoint(touchpoint, flow_name, other_heroes=other_heroes)

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
                touchpoint = generate_touchpoint(flow_name, step, prior_summaries, correction=correction, insight_brief=brief)
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
        "shape the what_happens_next markers are) and set that field directly to your own genuine read of "
        "what they asked for — don't just describe the visual change in a sentence and leave the field "
        "untouched, and don't invent a workaround in the text if a real field for it already exists.\n\n"
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
        "The note field is ONLY about this NEW feedback above, evaluated against the fields that actually "
        "exist in this schema right now — never about an earlier round in the history above (those were "
        "already resolved when they happened; don't re-litigate or re-explain them here), and never about "
        "a capability this schema doesn't have. Leave note null unless THIS feedback itself hits a real "
        "conflict right now."
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

    Retries up to MAX_RETRIES when the Sweeper fails the result, same
    correction-loop principle as revise_flow_touchpoint()/run_flow_pipeline -
    this used to ship a Sweeper-failing draft after exactly one attempt,
    which meant a real, catchable defect went straight to a human instead of
    a normal automatic retry fixing it first."""
    feedback_history = feedback_history or []

    query_question = _resolve_live_data_request(feedback, flow_name)
    live_data_context = _run_live_data_lookup(query_question) if query_question else None

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

        email = generate_email(flow_name, first_name, correction=correction)
        rendered = email["rendered_text"]
        sweep = sweep_email(rendered, flow_name=flow_name, hero_info=email["content"], human_feedback=feedback)
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
    touchpoint_n: Optional[int] = Field(
        default=None,
        description="The step number this feedback is clearly about, confidently determined from the "
        "real content of each touchpoint below (e.g. 'move the button to the right' matches whichever "
        "touchpoint's real CTA text was actually mentioned or is the only plausible match). Null if it "
        "doesn't confidently resolve to exactly one step.",
    )
    candidate_ns: List[int] = Field(
        default_factory=list,
        description="ONLY if touchpoint_n is null because the feedback genuinely matches two or more "
        "touchpoints equally (e.g. several touchpoints share the same CTA text and nothing else in the "
        "feedback narrows it down): list every one of those step numbers here, so the person can be asked "
        "a specific question naming just those steps instead of a generic 'which one'. Leave empty if "
        "touchpoint_n was resolved, or if the feedback matches nothing at all.",
    )
    reason: str = Field(description="One short line explaining the resolution (or why it's ambiguous/no match).")


def _touchpoint_content_summary(t: dict) -> str:
    content = t.get("content")
    if content is None:
        return "(failed to generate - nothing to reference)"
    if t["channel"] == "email":
        position = content.get("cta_position", 0.0) or 0.0
        return (
            f"subject {content['subject']!r}, hero {content.get('hero')}, has a real REPOSITIONABLE CTA "
            f"BUTTON reading {content['cta_text']!r} currently sitting at horizontal position "
            f"{position:.2f} (0.0=flush left, 0.5=centre, 1.0=flush right) - the only channel where a "
            f"request to move/align/reposition 'the button' is even possible"
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
    principle as analytics_agent._resolve_followup_question(). Fails open
    to no-match (touchpoint_n=None, candidate_ns=[]) on any error, so the
    caller's existing generic clarifying question still works as a
    fallback rather than the whole reply silently failing."""
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
        return {"touchpoint_n": None, "candidate_ns": [], "reason": "resolution unavailable"}
    return result.model_dump()


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
    what an earlier touchpoint already said).

    Retries up to MAX_RETRIES when the Sweeper fails the result (same
    correction-loop principle as run_flow_pipeline/_run_pipeline_loop) -
    this used to be a single shot that shipped a Sweeper-failing draft
    straight to Slack labelled "needs review" even when a normal automatic
    retry would have fixed it. Each retry keeps the human's original
    feedback in force (grounded on the draft that just came out of the
    previous attempt) while also handing the Copywriter the Sweeper's exact
    reasons to fix, same wording generate_flow's own retry loop uses."""
    feedback_history = feedback_history or []
    target = next((t for t in touchpoints if t["n"] == touchpoint_n), None)
    if target is None:
        raise ValueError(f"No touchpoint {touchpoint_n} in this flow (has {[t['n'] for t in touchpoints]}).")

    prior_summaries = [_touchpoint_summary(t) for t in touchpoints if t["n"] < touchpoint_n]
    step = {"n": target["n"], "channel": target["channel"], "timing": target["timing"], "intent": target["intent"]}
    other_heroes = _heroes_from_touchpoints(touchpoints, touchpoint_n)

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
            new_touchpoint = generate_touchpoint(flow_name, step, prior_summaries, correction=correction)
            sweep = _sweep_touchpoint(new_touchpoint, flow_name, other_heroes=other_heroes, human_feedback=feedback)
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

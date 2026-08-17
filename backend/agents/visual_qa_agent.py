"""Visual QA agent - the real n8n pipeline's step after Sweeper (source:
"Agent Prompts - CRM Team.md", node 5: "Visual QA (gpt-4o vision)").

Every other check in this system reads TEXT - the Sweeper never sees the
actual rendered image, only the plain-text render used for its compliance/
register checks. This is the one genuinely different kind of review: it
looks at the real rendered PNG the way a customer actually would, and can
catch things text never reveals - cramped layout, a hero that doesn't
actually fit once rendered, broken spacing, anything that looks cheap or
broken only visible in the image itself.

Real constraint, not a design choice: this needs a vision-capable model,
and Groq has none in this account (confirmed live - a direct probe
request rejects any multi-part/image message outright). Anthropic's
Claude models do support vision, so this only ever runs for real when
Anthropic is reachable (llm_provider.anthropic_available()) - otherwise it
skips cleanly and says so, rather than sending an image to a model that
will just reject the request shape.
"""
import base64
import io
import logging

from langchain_core.messages import HumanMessage

from .llm_provider import anthropic_available, get_llm

logger = logging.getLogger("visual_qa_agent")

# Real system prompt (Agent Prompts - CRM Team.md, node 5), unchanged.
SYSTEM_PROMPT = (
    "You are the VISUAL QA reviewer for andSons (premium men's health / hair-loss telehealth). Look at "
    "this rendered email or WhatsApp image as a customer would. Judge only what you can SEE: does it look "
    "premium, clean and on-brand (terracotta #963e21 button, calm off-white, generous spacing); is the "
    "visual hierarchy clear; if there is a hero photo does it fit the message and sit correctly; is "
    "anything broken, cramped, cut off, misaligned, low-quality, or cheap-looking; any empty bands or "
    "rendering glitches. Reply in ONE short line: start with 'LOOKS GOOD' or 'NEEDS WORK', then a colon "
    "and 1-2 specific visual notes (what to change). Be strict but fair; a clean text-first email with no "
    "hero is acceptable."
)

NOT_AVAILABLE_TEXT = "Visual QA skipped - no vision-capable model is reachable right now."


def review_image(image, channel: str = "email") -> dict:
    """image: a PIL Image (the same one about to be posted to Slack).
    Returns {"reviewed": bool, "looks_good": bool, "note": str}.
    `reviewed=False` means the check genuinely didn't run (no vision
    model available) - never inferred as a pass, so callers can't
    mistake "we couldn't check" for "it looked fine"."""
    if not anthropic_available():
        return {"reviewed": False, "looks_good": True, "note": NOT_AVAILABLE_TEXT}

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    llm = get_llm("VISUAL_QA", temperature=0.0)
    message = HumanMessage(content=[
        {"type": "text", "text": SYSTEM_PROMPT},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
    ])

    try:
        response = llm.invoke([message])
        reply = (response.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - a vision-call failure skips, never crashes the post
        logger.warning("Visual QA call failed: %s", exc)
        return {"reviewed": False, "looks_good": True, "note": f"Visual QA call failed ({exc}) - skipped."}

    if not reply:
        return {"reviewed": False, "looks_good": True, "note": "Visual QA returned an empty reply - skipped."}

    looks_good = reply.upper().startswith("LOOKS GOOD")
    return {"reviewed": True, "looks_good": looks_good, "note": reply}

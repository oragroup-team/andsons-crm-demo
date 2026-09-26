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

from .llm_provider import anthropic_available, extract_text_content, get_llm

logger = logging.getLogger("visual_qa_agent")

SYSTEM_PROMPT = (
    "You are the VISUAL QA reviewer for OVA Singapore (women's health telehealth). Look at this rendered "
    "image as a customer would. An OVA EMAIL is the team's real 4-zone marketing template (NOT a minimal "
    "doctor-written letter): a full-width dark-purple logo bar, then a hero banner (centred headline/"
    "subcopy/CTA button and a CIRCULAR real hero photo on a lavender backdrop), then a flexible body-"
    "content zone (a varying mix of plain text, product photos, dark section-header bars, 3-column icon "
    "grids, photo+text two-column modules, dark callout cards, and/or checklist cards - the exact mix and "
    "count legitimately varies email to email, never flag a short or long body for its length alone), then "
    "a full-width dark-purple closing footer (a heading, checkmark bullets, a button, and a second real "
    "photo bleeding off the edge), then a plain-text legal footer. A hero photo, buttons, icon grids, "
    "callout cards, and checklist cards are ALL CORRECT AND EXPECTED here - never flag any of those for "
    "merely being present. Only flag a REAL visual defect: a missing top hero or closing photo, cramped or "
    "overlapping text, a hero image that looks broken or oddly cropped, broken spacing, misalignment, "
    "overflow, low-quality rendering, or empty bands/glitches. A WhatsApp image is a normal WhatsApp chat "
    "card and is fine as long as it isn't broken. Reply in ONE short line: start with 'LOOKS GOOD' or "
    "'NEEDS WORK', then a "
    "colon and 1-2 specific visual notes."
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
        # Real, live-caught bug this fixes: with Opus 5's thinking, this
        # call's response.content came back as a list of content blocks
        # (not a plain string) often enough to matter, and .strip() on a
        # list raises - see llm_provider.extract_text_content()'s own
        # docstring for the confirmed repro.
        reply = extract_text_content(response).strip()
    except Exception as exc:  # noqa: BLE001 - a vision-call failure skips, never crashes the post
        logger.warning("Visual QA call failed: %s", exc)
        return {"reviewed": False, "looks_good": True, "note": f"Visual QA call failed ({exc}) - skipped."}

    if not reply:
        return {"reviewed": False, "looks_good": True, "note": "Visual QA returned an empty reply - skipped."}

    looks_good = reply.upper().startswith("LOOKS GOOD")
    return {"reviewed": True, "looks_good": looks_good, "note": reply}

"""Template reference agent - lets a human upload a real screenshot or
mockup and have the Copywriter build (or revise) toward its actual visual
structure, instead of only ever working from typed feedback. New
capability, added 2026-08-26 per explicit request: "We should be able to
upload an image and ask it to follow the template in the image for
mails/whatsapp."

Uses the same vision-capable model as visual_qa_agent.py (Claude only -
Groq has no vision model in this account, see that module's docstring for
the confirmed constraint) to describe a reference image in the SAME
vocabulary this system's own content fields already understand
(background colour, CTA button colour/position, whether a hero photo is
used and roughly how, whether a numbered/bulleted list of steps appears,
overall tone/formality). This is a real, structured reading of the image
mapped onto what the PIL renderer can actually draw - not a request to
literally recreate an arbitrary layout, which this renderer has no way to
do. The result is designed to be handed to the Copywriter as an
unusually detailed piece of design instruction, not treated as a separate
code path the rest of the pipeline has to special-case.
"""
import base64
import logging

from langchain_core.messages import HumanMessage

from .llm_provider import anthropic_available, extract_text_content, get_llm

logger = logging.getLogger("template_agent")

SYSTEM_PROMPT = """You are reading a real reference image (a screenshot or mockup a human reviewer uploaded) \
so the andSons CRM Copywriter can build or revise an email or WhatsApp message to follow its actual visual \
template - not copy its wording, its VISUAL STRUCTURE.

Describe, in plain English, ONLY what this system's own renderer can actually control:
- Background colour: a real hex code if the page/canvas background is a distinct colour, or say "standard \
brand default" if it's plain white/off-white.
- Hero photo: is there a photo at the top? Roughly what does it show (one plain sentence, no invented \
customer-facing wording), and does it carry a headline/text overlay?
- CTA button: its approximate colour and where it sits (flush left, centered, flush right, or roughly what \
fraction of the way across).
- A numbered/bulleted list of steps or bullet points, if the layout has one, and which marker style (plain \
numbers, icons, dots) it uses.
- Overall tone/formality the layout itself implies (e.g. minimal and text-heavy vs. bold and visual) - \
this is style guidance for the Copywriter's own writing register, not something to quote.

Say nothing about a structural element this renderer genuinely cannot draw (e.g. multi-column layouts, a \
different logo, animated elements) - if the image calls for something outside what a real reviewer's own \
feedback fields could already represent, say so plainly in one line instead of inventing a fake match. \
NEVER transcribe or invent customer-facing copy from the image - describe the STYLE only, never the words \
- and never invent a price, product name, or claim you see in the reference image; those still have to \
come from this system's own real, approved data, not from a photo."""

NOT_AVAILABLE_TEXT = (
    "Template reference image skipped - no vision-capable model is reachable right now, so this build/"
    "revision proceeded on the text request alone."
)


def analyze_template_image(image_bytes: bytes, mimetype: str = "image/png") -> str:
    """Returns a plain-text description of the reference image's visual
    template, in the vocabulary the Copywriter's own fields already
    understand - meant to be merged into the same correction/context text
    a human's own typed feedback already flows through, not treated as a
    separate mechanism. Fails safe: returns a plain explanatory string
    rather than raising, so an uploaded image never blocks the rest of a
    real request."""
    if not anthropic_available():
        return NOT_AVAILABLE_TEXT

    b64 = base64.b64encode(image_bytes).decode()
    llm = get_llm("VISUAL_QA", temperature=0.0)  # same vision-capable role visual_qa_agent.py uses
    message = HumanMessage(content=[
        {"type": "text", "text": SYSTEM_PROMPT},
        {"type": "image", "source": {"type": "base64", "media_type": mimetype, "data": b64}},
    ])
    try:
        response = llm.invoke([message])
        reply = extract_text_content(response).strip()
    except Exception as exc:  # noqa: BLE001 - never blocks the real request over a vision call failing
        logger.warning("Template image analysis call failed: %s", exc)
        return f"(template image analysis failed: {exc} - proceeding on the text request alone)"

    return reply or "(template image analysis returned nothing useful - proceeding on the text request alone)"

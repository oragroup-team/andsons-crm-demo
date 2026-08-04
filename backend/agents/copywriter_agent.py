"""Copywriter agent - drafts andSons CRM emails in the approved "mixed" style.

Input: flow_name (see backend/flows.py) + customer first_name (+ optional
correction instruction). Output: structured JSON grounded in the real
andSons brand voice, compliance rules, and per-flow briefs (source:
CRM_Email_Generation_Data/&SONS CRM Knowledge).
"""
import re
from typing import List, Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from flows import FLOW_BY_SLUG
from image_bank import HERO_BANK, HERO_KEYS
from text_sanitize import sanitize_text

from .llm_provider import get_llm

_LEADING_NUMBER_RE = re.compile(r"^\s*\d+[.)]\s*")
_LEADING_GREETING_RE = re.compile(r"^\s*hi\s+[^\s,]+\s*,\s*", re.IGNORECASE)


def _strip_leading_number(step: str) -> str:
    """Defensively strip a self-numbered prefix (e.g. '1. ') the model may
    add despite instructions, so rendering never double-numbers steps."""
    return _LEADING_NUMBER_RE.sub("", step).strip()


def _strip_leading_greeting(line: str) -> str:
    """Defensively strip a duplicate 'Hi <name>,' the model may put at the
    start of opening_lines[0], since render_email always adds its own
    greeting line before the opening lines."""
    return _LEADING_GREETING_RE.sub("", line).strip()


# The real "P1 Email 1 - Approved Golden Template (mixed style)" doc,
# Thalia-approved 2026-07-07 - the P1 quality floor and style reference for
# every flow's tone/restraint. Footer is the real WhatsApp CS link + the
# real registered address (02-Compliance-Claims.md rule 5).
GOLDEN_P1_REFERENCE = """Subject: Your hair loss treatment plan is ready
Preheader: One step left to begin your treatment.

Hi [name],

Your hair loss treatment plan is ready. Your doctor put it together
during your consultation, around what you discussed.

Deciding to treat hair loss is a big step, and you've already taken it
by speaking with a doctor.

What happens next
1. Your doctor has finalised your personalised plan.
2. Start your treatment below. It takes a minute.
3. It arrives discreetly, and you begin when you're ready.

Hair loss tends to progress gradually, so treatment works best when it
begins early.

[Start My Treatment]

Doctor-led plan · Clinically studied · Discreet delivery

The andSons team

WhatsApp customer service: https://api.whatsapp.com/message/VX2SIFBLE7ECI1?autoload=1&app_absent=0
andSons Pte. Ltd., 1 Fusionopolis Place, #17-10, Galaxis, Singapore 138522
Unsubscribe"""

FOOTER_LINES = [
    "WhatsApp customer service: https://api.whatsapp.com/message/VX2SIFBLE7ECI1?autoload=1&app_absent=0",
    "andSons Pte. Ltd., 1 Fusionopolis Place, #17-10, Galaxis, Singapore 138522",
    "Unsubscribe",
]

SYSTEM_PROMPT = """You are the senior CRM copywriter for andSons, a 100% online men's health telehealth \
brand in Singapore (licensed doctors, discreet delivery). You write in the Juniper/Eucalyptus register: \
calm, direct, medically literate, reassuring, warm, personal, plainspoken. Short one-line paragraphs with \
breathing room. Talks to ONE person, never a segment.

Use this approved P1 golden template as your style and restraint reference (tone, sentence length, \
warmth) even when writing for a different flow - match its register, never copy it verbatim for a \
different flow:

---
{golden_reference}
---

LANGUAGE RULES (absolute, every flow):
- British English spelling everywhere (personalised, customised, recognise, colour, programme) - never \
American spelling.
- NEVER use em-dashes or long dashes as punctuation. Use full stops or commas instead.
- No exclamation marks. Sentence case (not Title Case).
- No app-speak: never "activate", "tap", "unlock". A plan is "confirmed" or "reviewed", never "activated".
- Signature is always "The andSons team" with no dash before it. Do not write a footer, unsubscribe line, \
or address - those are appended automatically after your copy.
- NO DEFENSIVE META-COMMENTARY: never narrate the email's intent or what it is NOT doing. Banned phrases: \
"we're not selling you anything", "this isn't a sales pitch", "the only reason we're reaching out", "we \
just wanted to". State the message plainly; confident brands never explain themselves.
- PAYMENT-FRAMING BAN: never lead with money. The word "payment" must NOT appear in the subject, \
preheader, or CTA text (prefer "complete the remaining step" / "start your treatment" / "confirm your \
delivery"). The body may reference a payment issue only when the flow is genuinely about billing (e.g. \
Replenishment/Dunning), stated plainly and without guilt.
- CTA convention: verb + "My" + noun, e.g. "Start My Treatment", "Book My Free Consultation", "Complete \
My Order", "Confirm My Next Delivery" - match the flow's suggested CTA below unless a closer variant \
reads more naturally.

COMPLIANCE (hard rules, every flow):
- NEVER name a prescription medicine (Minoxidil, Finasteride, or any drug name) anywhere in the copy. Rx \
treatment is only ever "your doctor's plan" / "your treatment plan" / "prescription options" / \
"doctor-guided treatment".
- Treatment decisions belong to the doctor, never the brand - attribute plans/decisions to "your doctor".
- NEVER claim or imply the customer can contact the doctor directly ("message your doctor anytime") - \
support routes through customer service (WhatsApp), never the doctor.
- Brand name is exactly "andSons" - never "&Sons".
- Invent nothing: no fabricated statistics, social proof, testimonials, ratings, step counters, badges, \
seals, deadlines, or features. If a clinical stat is used, it MUST carry a source footnote (DOI: \
10.1111/dth.12246) plus the sentence "Individual results vary." - never state a stat without both.
- No cure/guarantee language ("cure baldness", "guaranteed regrowth", "100% works"). No shame or \
fear-based pressure. No fake urgency or countdown framing - the only legitimate motivator is the real, \
calmly-stated fact that hair loss is progressive so starting early protects more of what he still has.
- This email may send the same day as the underlying trigger event (e.g. a consultation or quiz). Never \
imply days have passed or that the customer has been deliberating ("taking a few days", "you've been \
thinking it over") unless the flow brief says otherwise.

STRUCTURE (per email, use judgement - these are OPTIONAL composable blocks, not a fixed template):
- opening_lines: 2-3 short warm paragraphs, empathy before logistics - acknowledge the step he's already \
taken before pointing at the next one.
- what_happens_next (OPTIONAL): a short numbered block (2-3 short lines) ONLY when it genuinely adds \
clarity for this moment. Omit it (leave null) for a simpler, more personal email - restraint is the \
luxury here, not decoration.
- trust_line (OPTIONAL): a thin centred line like "Doctor-led plan · Clinically studied · Discreet \
delivery", plain text separated by " · ", never a description of badge graphics. Include only when it \
adds confidence; omit for a simpler email.
- gentle_truth_line (OPTIONAL): one gentle, caring true statement relevant to this flow's moment (for \
hair-loss-timing flows: the progressive-condition truth, stated as care not urgency). Omit if nothing \
true and relevant fits.
- Never more than one of each block. Never fabricate content to fill a block - an empty/omitted block is \
always better than an invented one.

HERO IMAGE SELECTION (per-email judgement, not a default habit): a hero is OPTIONAL - use one only when \
it genuinely strengthens THIS message; a clean text-first email (hero: "none") is often more premium and \
personal than a forced photo. The image is an argument: it must argue the same thing the copy argues, at \
the same emotional moment. Never repeat a hero out of habit; a weak or ill-fitting image is worse than no \
image. Choose ONLY from the approved photo bank below - never invent or request a new image.
Choose exactly one of: {hero_keys}, or "none".
- "smiling" and "adjusting" are LOCKED heroes with a headline already baked into the image file - do NOT \
set hero_headline for these (leave it null); adding one would duplicate the text on the image.
- {raw_hero_keys} are RAW photos with no baked text - if you use one, set hero_headline to a short, PLAIN, \
CONCRETE, literal headline (2-5 words a real person would say, e.g. "Your plan is ready", "Why starting \
early matters"). Never poetic/abstract wordplay ("the window worth protecting"). Sentence case, no dash.
- If none of the bank photos genuinely fit this email's moment, choose "none" - a text-first email is \
always a valid, often better choice than forcing a mismatched photo.
- Always set hero_rationale to one short line: why this hero (or "none") fits this specific moment.

FLOW FOR THIS EMAIL: {flow_name}
{flow_brief}

Address the customer by first name: {first_name}.
"""


def _build_flow_brief(flow_slug: str) -> str:
    flow = FLOW_BY_SLUG.get(flow_slug)
    if flow is None:
        flow = FLOW_BY_SLUG["p1_plan_not_purchased"]

    lines = [
        f"Flow: {flow['label']} (track: {flow['track']}).",
        f"Reader: {flow['audience']}",
        f"Goal: {flow['goal']}",
        f"Suggested CTA for this flow: something like \"{flow['cta']}\".",
    ]
    if flow["allow_price"]:
        lines.append(
            "You MAY reference a real product and its real price if it strengthens this message - "
            "the approved OTC catalogue is: 3% Redensyl Anti-Hair Loss Serum ($42), Intense Hair Growth "
            "Trio ($78), Intense Hair Growth Kit ($78). Never invent a different price or product."
        )
    else:
        lines.append(
            "HARD CONSTRAINT: never mention a price, a dollar amount, or a discount code in this email."
        )
    if flow["track"] == "rx":
        lines.append(
            "This is the Rx track - the highest compliance sensitivity. Never imply the customer can "
            "change or stop prescribed treatment themselves."
        )
    return "\n".join(lines)


class EmailContent(BaseModel):
    subject: str = Field(description="Email subject line")
    preheader: str = Field(description="Short preheader/preview text, one sentence")
    opening_lines: List[str] = Field(
        description="2-3 short warm opening paragraphs (each a single line/sentence), Juniper voice. "
        "Do NOT start the first line with a greeting like 'Hi [name],' — the greeting is rendered "
        "separately, before these lines."
    )
    what_happens_next: Optional[List[str]] = Field(
        default=None,
        description="OPTIONAL judgement call: 2-3 short steps describing what happens next, only when "
        "it genuinely adds clarity. Each string is just the step's sentence, with NO leading "
        "number/bullet. Omit (null) for a simpler email.",
    )
    gentle_truth_line: Optional[str] = Field(
        default=None,
        description="OPTIONAL: one gentle, caring true sentence relevant to this flow's moment (care, "
        "not urgency). Omit if nothing true and relevant fits this flow.",
    )
    cta_text: str = Field(description="The call-to-action button text, e.g. 'Start My Treatment'")
    trust_line: Optional[str] = Field(
        default=None,
        description="OPTIONAL: short centred trust line, plain text separated by ' · ', e.g. "
        "'Doctor-led plan · Clinically studied · Discreet delivery'. Omit for a simpler email.",
    )
    hero: Literal[tuple(HERO_KEYS)] = Field(
        description="Which hero image to use: one of the approved bank keys, or 'none' for a deliberate "
        "text-first email."
    )
    hero_headline: Optional[str] = Field(
        default=None,
        description="Short, plain, concrete overlay headline (2-5 words) for a RAW bank photo. Leave "
        "null for 'smiling'/'adjusting' (already baked in) and for 'none'.",
    )
    hero_rationale: Optional[str] = Field(
        default=None,
        description="One short line: why this hero (or 'none') fits this specific email's moment.",
    )


def _sanitize_content(content: EmailContent) -> EmailContent:
    """Run every text field through the shared sanitizer so no em-dash,
    non-breaking hyphen, curly quote, or stray markdown reaches customer
    copy - the real andSons house rule bans em-dashes/long-dashes outright."""
    return content.model_copy(
        update={
            "subject": sanitize_text(content.subject),
            "preheader": sanitize_text(content.preheader),
            "opening_lines": [sanitize_text(line) for line in content.opening_lines],
            "what_happens_next": (
                [_strip_leading_number(sanitize_text(step)) for step in content.what_happens_next]
                if content.what_happens_next
                else content.what_happens_next
            ),
            "gentle_truth_line": (
                sanitize_text(content.gentle_truth_line) if content.gentle_truth_line else None
            ),
            "cta_text": sanitize_text(content.cta_text),
            "trust_line": sanitize_text(content.trust_line) if content.trust_line else None,
            "hero_headline": sanitize_text(content.hero_headline) if content.hero_headline else None,
            "hero_rationale": sanitize_text(content.hero_rationale) if content.hero_rationale else None,
        }
    )


def resolve_hero(content: EmailContent) -> dict:
    """Turn the Copywriter's hero decision into an actual displayable image.
    Bank keys resolve to their real approved local URL; 'none' stays
    text-first. Baked heroes ('smiling'/'adjusting') never carry a separate
    overlay headline, since the image already has one. Selection is
    bank-only - there is no generation fallback."""
    hero = content.hero
    reasons = []

    if hero in HERO_BANK:
        bank_entry = HERO_BANK[hero]
        is_baked = bank_entry["baked_headline"] is not None
        headline = bank_entry["baked_headline"] if is_baked else content.hero_headline
        if is_baked and content.hero_headline:
            reasons.append(
                f"Dropped a redundant overlay headline on the '{hero}' hero - it already has a baked-in headline."
            )
        return {
            "hero": hero,
            "hero_image_url": bank_entry["url"],
            "hero_headline": headline,
            "hero_source": "bank",
            "hero_rationale": content.hero_rationale,
            "hero_notes": reasons,
        }

    return {
        "hero": "none",
        "hero_image_url": None,
        "hero_headline": None,
        "hero_source": "none",
        "hero_rationale": content.hero_rationale,
        "hero_notes": reasons,
    }


def render_email(content: EmailContent, first_name: str, hero_info: Optional[dict] = None) -> str:
    """Render the structured email content into a plain-text block, in the
    same shape as the golden template, so the Sweeper agent can evaluate it."""
    body = (
        _strip_leading_greeting(content.opening_lines[0].replace("[name]", first_name))
        if content.opening_lines
        else ""
    )
    other_lines = "\n\n".join(
        line.replace("[name]", first_name) for line in content.opening_lines[1:]
    )

    parts = []
    if hero_info and hero_info.get("hero") != "none" and hero_info.get("hero_image_url"):
        hero_key = hero_info["hero"]
        description = HERO_BANK.get(hero_key, {}).get("description", "hero image")
        headline = hero_info.get("hero_headline")
        headline_part = f' — overlay headline: "{headline}"' if headline else ""
        parts.append(f'[HERO IMAGE: {description}{headline_part}]')
        parts.append("")

    parts += [
        f"Subject: {content.subject}",
        f"Preheader: {content.preheader}",
        "",
        f"Hi {first_name},",
        "",
        body,
    ]
    if other_lines:
        parts.append("")
        parts.append(other_lines)

    if content.what_happens_next:
        steps = "\n".join(
            f"{i + 1}. {_strip_leading_number(step)}"
            for i, step in enumerate(content.what_happens_next)
        )
        parts += ["", "What happens next", steps]

    if content.gentle_truth_line:
        parts += ["", content.gentle_truth_line]

    parts += ["", f"[{content.cta_text}]"]

    if content.trust_line:
        parts += ["", content.trust_line]

    parts += ["", "The andSons team", ""] + FOOTER_LINES
    return "\n".join(parts).strip()


def generate_email(flow_name: str, first_name: str, correction: Optional[str] = None) -> dict:
    """Run the Copywriter agent. If `correction` is provided, it is appended
    to the ORIGINAL system prompt/constraints (never sent alone) so the model
    keeps the full brand context on every retry."""
    llm = get_llm("COPYWRITER")
    structured_llm = llm.with_structured_output(EmailContent)

    flow_brief = _build_flow_brief(flow_name)
    raw_hero_keys = [k for k, v in HERO_BANK.items() if v["baked_headline"] is None]
    system_text = SYSTEM_PROMPT.format(
        golden_reference=GOLDEN_P1_REFERENCE,
        flow_name=flow_name,
        flow_brief=flow_brief,
        first_name=first_name,
        hero_keys=", ".join(f'"{k}"' for k in HERO_BANK.keys()),
        raw_hero_keys=", ".join(f'"{k}"' for k in raw_hero_keys),
    )

    human_text = f"Write the {flow_name} email for {first_name}."
    if correction:
        human_text += (
            "\n\nThis is a REVISION. The previous draft failed brand review. Keep every rule above "
            "in force and additionally apply this correction:\n" + correction
        )

    prompt = ChatPromptTemplate.from_messages(
        [("system", system_text), ("human", human_text)]
    )
    chain = prompt | structured_llm
    content: EmailContent = chain.invoke({})
    content = _sanitize_content(content)

    hero_info = resolve_hero(content)
    rendered = render_email(content, first_name, hero_info=hero_info)

    result_content = content.model_dump()
    result_content.update(
        {
            "hero": hero_info["hero"],
            "hero_image_url": hero_info["hero_image_url"],
            "hero_headline": hero_info["hero_headline"],
            "hero_source": hero_info["hero_source"],
        }
    )
    return {
        "content": result_content,
        "rendered_text": rendered,
        "hero_notes": hero_info["hero_notes"],
    }

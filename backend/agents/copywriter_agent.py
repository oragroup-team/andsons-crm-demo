"""Copywriter agent - drafts andSons CRM emails in the approved "mixed" style.

Input: flow_name (see backend/flows.py) + customer first_name (+ optional
correction instruction). Output: structured JSON grounded in the real
andSons brand voice, compliance rules, and per-flow briefs (source:
CRM_Email_Generation_Data/&SONS CRM Knowledge).
"""
import logging
import re
from typing import List, Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from flows import FLOW_BY_SLUG, VALID_FLOW_SLUGS
from image_bank import HERO_BANK, HERO_KEYS
from text_sanitize import sanitize_text

from .llm_provider import get_llm, invoke_with_retry

logger = logging.getLogger("copywriter_agent")

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
- WRITE LIKE A SHARP HUMAN, NOT A BROCHURE: every line - subject, preheader, opening lines, hero \
headline, CTA - should sound like something a smart, warm person would actually say out loud, never a \
clinical label or a form field. Avoid flat administrative nouns standing in for the real moment: \
"consult" / "consultation" (say "talk to a doctor", "your doctor call" instead), "appointment", \
"engagement", "assessment" (say "the quiz", "what you told us"), "solution" for a product. Prefer a \
concrete verb and a specific detail over an abstract noun. If a line could be pasted into any generic \
telehealth brand's email unchanged, it's too generic - make it specific to andSons and this moment.

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

HERO IMAGE SELECTION (per-email judgement, but use one whenever a real photo fits): a hero photo is the \
default for this brand - these are real marketing emails, and a photo makes the moment feel human, not \
just a wall of text. Go through the approved photo bank below and actually look at what each photo shows \
and the moment it's built for; in the large majority of emails, at least one of them genuinely fits THIS \
message's moment, and that's the one to use. The image is an argument: it must argue the same thing the \
copy argues, at the same emotional moment - so pick deliberately, not the first one that sounds vaguely \
safe, and across many emails draw from across this whole bank, not one or two favourites. Reserve \
hero: "none" for the genuine minority of cases where you've actually checked the whole bank and nothing \
in it fits this specific moment - it is not an equally-weighted default, and it is never a shortcut for \
"didn't feel like picking one." Never repeat a hero out of habit, and never force a mismatched photo just \
to have one - a weak or ill-fitting image is worse than no image, but a genuinely fitting one beats "none" \
almost every time. Choose ONLY from the approved photo bank below - never invent or request a new image.

APPROVED PHOTO BANK:
{hero_catalog}

- "smiling" and "adjusting" are LOCKED heroes with a headline already baked into the image file - do NOT \
set hero_headline for these (leave it null); adding one would duplicate the text on the image.
- Every other key is a RAW photo with no baked text - if you use one, set hero_headline to a short, \
CONCRETE line (2-5 words, strictly - count them) a real person would actually SAY out loud in \
conversation, not a label or a feature name. Never a formal/clinical noun phrase like "Your private \
consult" or "Your treatment appointment" - those are things a brochure calls a thing, not something a \
person says. Prefer a plain sentence fragment with a verb or a feeling in it, all within 2-5 words. \
These are ILLUSTRATIVE STYLE EXAMPLES ONLY, to show the register - never reuse one of them verbatim, \
write a fresh line specific to what THIS email is actually about: "Your plan is ready", "Small \
changes, real difference", "Worth ten minutes", "See what changed". Never poetic/abstract wordplay \
either ("the window worth protecting"). Sentence case, no dash.
- If none of the bank photos genuinely fit this email's moment, choose "none" - a text-first email is \
always a valid, often better choice than forcing a mismatched photo.
- Always set hero_rationale to one short line: why this hero (or "none") fits this specific moment.

FLOW FOR THIS EMAIL: {flow_name}
{flow_brief}

Address the customer by first name: {first_name}.
"""


def _build_hero_catalog() -> str:
    """Render the full hero bank - key, what the photo actually shows, and
    the moment it's built for - so the Copywriter chooses based on real
    content instead of guessing from a bare key name (that gap was why it
    kept defaulting to the same one or two 'safe-sounding' keys)."""
    lines = []
    for key, entry in HERO_BANK.items():
        locked = " [LOCKED - headline already baked in, do not add hero_headline]" if entry["baked_headline"] else ""
        lines.append(f'- "{key}"{locked}: {entry["description"]}. Best for: {entry["moment"]}.')
    return "\n".join(lines)


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
        bank_entry = HERO_BANK.get(hero_key, {})
        description = bank_entry.get("description", "hero image")
        headline = hero_info.get("hero_headline")
        is_baked = bank_entry.get("baked_headline") is not None
        # Label matters for the Sweeper: a baked-in headline is part of the
        # approved image file itself (exempt from the 2-5 word overlay
        # rule), never text the Copywriter wrote - mislabelling it as an
        # "overlay headline" makes the Sweeper wrongly fail a real, already
        # approved hero photo.
        if headline:
            label = "baked-in headline (already part of the approved image, not subject to the overlay word-count rule)" if is_baked else "overlay headline"
            headline_part = f' — {label}: "{headline}"'
        else:
            headline_part = ""
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


_INSIGHT_BRIEF_INSTRUCTION = """INTERNAL STRATEGY CONTEXT (why this email is being written right now) - \
this is real business/marketing data gathered to inform your ANGLE and EMPHASIS only. It is NOT \
customer-facing content:
- Never quote a raw number, percentage, table/column name, SQL, campaign ID, or phrase like "engagement \
dropped" / "sales are down" / "we noticed you..." anywhere in the copy - a customer must never sense this \
email exists because of an internal metric.
- Use it only to decide which real, already-approved benefit or reassurance to lead with, and how much \
urgency (still never fake urgency) the moment genuinely calls for.
- Every compliance rule above still applies in full - this context does not unlock a new stat, price, or \
claim; a clinical stat still needs its DOI footnote regardless of anything mentioned here.

{brief}
"""


def generate_email(
    flow_name: str,
    first_name: str,
    correction: Optional[str] = None,
    insight_brief: Optional[str] = None,
) -> dict:
    """Run the Copywriter agent. If `correction` is provided, it is appended
    to the ORIGINAL system prompt/constraints (never sent alone) so the model
    keeps the full brand context on every retry. If `insight_brief` is
    provided (from agents.insight_agent), it's included as internal strategy
    context only - see _INSIGHT_BRIEF_INSTRUCTION for the leak-prevention
    rules enforced around it."""
    llm = get_llm("COPYWRITER")
    structured_llm = llm.with_structured_output(EmailContent)

    flow_brief = _build_flow_brief(flow_name)
    system_text = SYSTEM_PROMPT.format(
        golden_reference=GOLDEN_P1_REFERENCE,
        flow_name=flow_name,
        flow_brief=flow_brief,
        first_name=first_name,
        hero_catalog=_build_hero_catalog(),
    )
    if insight_brief:
        system_text += "\n\n" + _INSIGHT_BRIEF_INSTRUCTION.format(brief=insight_brief)

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

    content, last_exc = invoke_with_retry(chain, label="Copywriter structured-output call")
    if content is None:
        raise RuntimeError(f"Copywriter failed to produce a draft after retrying ({last_exc}).") from last_exc

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


# --- Multi-touchpoint flow generation ---------------------------------------
# Real andSons flows are not one email - they're a whole MoEngage journey of
# several Email + WhatsApp touchpoints spaced over days/weeks (see flows.py's
# "cadence" field, sourced from the real P1 Build Packet and the live n8n
# Copywriter's own "author the ENTIRE flow" instruction in "Agent Prompts -
# CRM Team.md"). This section generates every touchpoint in a flow's real
# cadence, so a Slack request for "the P1 email" returns the whole sequence
# in one go, not just touchpoint 1.

WHATSAPP_SYSTEM_PROMPT = """You are the senior CRM copywriter for andSons, a 100% online men's health \
telehealth brand in Singapore. You are writing ONE WhatsApp touchpoint in a real multi-step andSons \
lifecycle flow - not an email. WhatsApp is a short, timely, personal nudge, never a shrunk-down email: \
plain conversational text, 2-4 short lines, warm and direct, no subject line (it is a chat message, not \
an email). It uses the real andSons WABA "Call-To-Action" template shape: a link-preview title above the \
message, then the message body (its first line is a short bold greeting/hook), then exactly one button.

The message body itself stays plain text - no HTML, no markdown, no image pasted into the body. The \
link-preview title and the one CTA button are the template's own structured fields, not something you \
write inline in the message.

Use this approved P1 style reference for register only (not content - that email is a different \
touchpoint):
---
{golden_reference}
---

LANGUAGE RULES (absolute): British English spelling everywhere (personalised, customised, recognise, \
colour, programme) - never American spelling. NEVER use em-dashes or long dashes as punctuation - use \
full stops or commas. No exclamation marks. Sentence case. No app-speak ("activate", "tap", "unlock").

COMPLIANCE (hard rules): NEVER name a prescription medicine anywhere. Treatment decisions belong to the \
doctor, never the brand. NEVER claim the customer can message the doctor directly - support is customer \
service on WhatsApp. Brand name is exactly "andSons". Invent nothing: no fabricated stats, social proof, \
counters, badges, deadlines. No cure/guarantee language, no shame, no fake urgency.
{price_rule}

NO DEFENSIVE META-COMMENTARY: never narrate the message's intent or what it is NOT doing.
PAYMENT-FRAMING BAN: never lead with money; the action is starting/continuing treatment, not paying.

FLOW: {flow_name}
{flow_brief}

THIS TOUCHPOINT'S MOMENT IN THE FLOW (timing: {timing}): {intent}

{prior_context}

Address the customer as NAME (a literal placeholder token, not a real name - never invent one).
Write ONLY the message body text (2-4 short lines) - no link, no CTA button markup, that's handled \
separately.
"""


class WhatsAppContent(BaseModel):
    link_title: str = Field(
        description="Short headline for the WhatsApp link-preview card that appears above the message "
        "(the real andSons WABA template format - a rich link preview, like a page title, not a fabricated "
        "claim) - e.g. 'Reimagining Men's Health by Teleconsultation with Doctor | andSons'. Plain, "
        "on-brand, describes what andSons/the linked page is, never a specific unverifiable claim."
    )
    hook_line: str = Field(
        description="ONE short, warm, direct greeting/hook sentence addressed to NAME - the first line "
        "the customer sees, shown BOLD in the real template, e.g. 'Hi NAME, still thinking it over?'."
    )
    body: str = Field(
        description="1-3 more short plain-text lines/sentences AFTER the hook line, shown in regular "
        "weight - the rest of the message. Together with the hook line, 2-4 short lines total. No "
        "signature, no link pasted into the text."
    )
    cta_text: str = Field(
        description="The single action this message points to, in the same verb + My + noun convention "
        "as email CTAs, e.g. 'Complete My Order' - shown as the one button this WhatsApp message carries."
    )


def _sanitize_whatsapp(content: WhatsAppContent) -> WhatsAppContent:
    return content.model_copy(
        update={
            "link_title": sanitize_text(content.link_title),
            "hook_line": sanitize_text(content.hook_line),
            "body": sanitize_text(content.body),
            "cta_text": sanitize_text(content.cta_text),
        }
    )


def render_whatsapp(content: WhatsAppContent) -> str:
    """Plain-text rendering of a WhatsApp touchpoint, in the same
    Sweeper-readable shape as render_email() - the link-preview title, the
    bold hook line, the rest of the message, then its one CTA button line,
    never HTML."""
    return f"[LINK PREVIEW: {content.link_title}]\n\n{content.hook_line}\n{content.body}\n\n[{content.cta_text}]"


def _prior_touchpoints_context(prior: list) -> str:
    if not prior:
        return "This is the FIRST touchpoint in the flow - nothing has been sent yet."
    used_heroes = [p["hero"] for p in prior if p.get("hero") and p["hero"] != "none"]
    lines = ["EARLIER TOUCHPOINTS ALREADY SENT in this same flow, so this one must feel like the next "
             "step in one continuous conversation, never a repeat of an earlier angle or opening line:"]
    for p in prior:
        lines.append(f"- Touchpoint {p['n']} ({p['channel']}, {p['timing']}): {p['summary']}")
    if used_heroes:
        lines.append(
            f"Heroes already used in this flow: {', '.join(used_heroes)} - NEVER reuse any of these; "
            "pick a different bank photo or 'none'."
        )
    return "\n".join(lines)


def generate_flow(flow_name: str, insight_brief: Optional[str] = None) -> dict:
    """Generate every real touchpoint in a flow's cadence (flows.py), in
    order, each aware of what earlier touchpoints in the same flow already
    said (so the sequence reads as one continuous journey, and no hero
    image or opening line repeats). Always addresses the NAME placeholder -
    there is no real customer in a Slack conversation to name. Returns
    {"flow_name", "touchpoints": [...]} - each touchpoint has "channel",
    "timing", "intent", "rendered_text", "content", and (email only) hero
    fields, ready for the Sweeper and the image renderers."""
    flow = FLOW_BY_SLUG.get(flow_name)
    if flow is None:
        raise ValueError(f"Unknown flow: {flow_name}")

    touchpoints = []
    prior_summaries = []

    for step in flow["cadence"]:
        if step["channel"] == "email":
            touchpoint = generate_flow_email_touchpoint(flow_name, step, prior_summaries, insight_brief=insight_brief)
        else:
            touchpoint = generate_flow_whatsapp_touchpoint(flow_name, step, prior_summaries, insight_brief=insight_brief)
        touchpoints.append(touchpoint)
        prior_summaries.append(_touchpoint_summary(touchpoint))

    return {"flow_name": flow_name, "touchpoints": touchpoints}


def _touchpoint_summary(touchpoint: dict) -> dict:
    if touchpoint["channel"] == "email":
        content = touchpoint["content"]
        summary = f"Subject '{content['subject']}' - {content['opening_lines'][0] if content.get('opening_lines') else ''}"
    else:
        summary = f"{touchpoint['content']['hook_line']} {touchpoint['content']['body']}"
    return {
        "n": touchpoint["n"],
        "channel": touchpoint["channel"],
        "timing": touchpoint["timing"],
        "hero": touchpoint["hero"],
        "summary": summary,
    }


def generate_flow_email_touchpoint(
    flow_name: str, step: dict, prior_summaries: list, correction: Optional[str] = None,
    insight_brief: Optional[str] = None,
) -> dict:
    """Generate (or regenerate, with `correction`) ONE email touchpoint of a
    flow's real cadence - shares the exact same prompt machinery as
    generate_email(), plus this touchpoint's real timing/intent and what
    earlier touchpoints in the same flow already said (so a Sweeper-driven
    retry stays aware of the rest of the sequence, not just its own text)."""
    flow = FLOW_BY_SLUG[flow_name]
    flow_brief = _build_flow_brief(flow_name)
    llm = get_llm("COPYWRITER")
    structured_llm = llm.with_structured_output(EmailContent)
    system_text = SYSTEM_PROMPT.format(
        golden_reference=GOLDEN_P1_REFERENCE,
        flow_name=flow_name,
        flow_brief=flow_brief,
        first_name="NAME",
        hero_catalog=_build_hero_catalog(),
    )
    system_text += (
        f"\n\nTHIS TOUCHPOINT'S MOMENT IN THE FLOW (touchpoint {step['n']} of "
        f"{len(flow['cadence'])}, timing: {step['timing']}): {step['intent']}\n\n"
        + _prior_touchpoints_context(prior_summaries)
    )
    if insight_brief:
        system_text += "\n\n" + _INSIGHT_BRIEF_INSTRUCTION.format(brief=insight_brief)
    human_text = f"Write touchpoint {step['n']} ({step['timing']}) of the {flow_name} flow for NAME."
    if correction:
        human_text += (
            "\n\nThis is a REVISION. The previous draft of this touchpoint failed brand review. Keep "
            "every rule above in force and additionally apply this correction:\n" + correction
        )

    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm

    content, last_exc = invoke_with_retry(chain, label="Copywriter flow touchpoint call")
    if content is None:
        raise RuntimeError(f"Copywriter failed to produce touchpoint {step['n']} ({last_exc}).") from last_exc

    content = _sanitize_content(content)
    hero_info = resolve_hero(content)
    rendered = render_email(content, "NAME", hero_info=hero_info)
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
        "n": step["n"],
        "channel": "email",
        "timing": step["timing"],
        "intent": step["intent"],
        "content": result_content,
        "rendered_text": rendered,
        "hero": hero_info["hero"],
    }


def generate_flow_whatsapp_touchpoint(
    flow_name: str, step: dict, prior_summaries: list, correction: Optional[str] = None,
    insight_brief: Optional[str] = None,
) -> dict:
    """Generate (or regenerate, with `correction`) ONE WhatsApp touchpoint
    of a flow's real cadence. See generate_flow_email_touchpoint()."""
    flow = FLOW_BY_SLUG[flow_name]
    flow_brief = _build_flow_brief(flow_name)
    price_rule = (
        "" if flow["allow_price"] else
        "HARD CONSTRAINT: never mention a price, a dollar amount, or a discount code in this message."
    )
    llm = get_llm("COPYWRITER")
    structured_llm = llm.with_structured_output(WhatsAppContent)
    system_text = WHATSAPP_SYSTEM_PROMPT.format(
        golden_reference=GOLDEN_P1_REFERENCE,
        flow_name=flow_name,
        flow_brief=flow_brief,
        timing=step["timing"],
        intent=step["intent"],
        price_rule=price_rule,
        prior_context=_prior_touchpoints_context(prior_summaries),
    )
    if insight_brief:
        system_text += "\n\n" + _INSIGHT_BRIEF_INSTRUCTION.format(brief=insight_brief)
    human_text = f"Write touchpoint {step['n']} ({step['timing']}) of the {flow_name} flow, a WhatsApp message for NAME."
    if correction:
        human_text += (
            "\n\nThis is a REVISION. The previous draft of this touchpoint failed brand review. Keep "
            "every rule above in force and additionally apply this correction:\n" + correction
        )

    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm

    content, last_exc = invoke_with_retry(chain, label="Copywriter WhatsApp touchpoint call")
    if content is None:
        raise RuntimeError(f"Copywriter failed to produce touchpoint {step['n']} ({last_exc}).") from last_exc

    content = _sanitize_whatsapp(content)
    rendered = render_whatsapp(content)
    return {
        "n": step["n"],
        "channel": "whatsapp",
        "timing": step["timing"],
        "intent": step["intent"],
        "content": content.model_dump(),
        "rendered_text": rendered,
        "hero": None,
    }


class EmailIntent(BaseModel):
    mode: Literal["direct", "insight"] = Field(
        description="'direct' if this is a plain request naming (or clearly implying) one specific "
        "flow, e.g. 'write a P1 email for Marcus'. 'insight' if this describes a business problem/signal "
        "and asks for an email to address it, e.g. 'OTC serum sales are down, write something to fix "
        "it' or 'winback isn't converting, draft an email for Wei about it' - i.e. the flow should be "
        "figured out FROM investigating the signal, not just matched from the wording. Also 'insight' "
        "whenever the message explicitly asks for the email to be grounded in real/live data - e.g. "
        "mentions MoEngage, BigQuery, a dashboard, 'live data', 'based on what's happening' - EVEN IF "
        "it also names a specific flow/customer (e.g. 'based on the live MoEngage flows, write a cart "
        "abandon email for Mark' is 'insight', not 'direct' - a flow name being present doesn't override "
        "an explicit request to actually check real data first; skipping that investigation because a "
        "flow was also named would silently ignore what was actually asked for)."
    )
    flow_name: Optional[Literal[tuple(VALID_FLOW_SLUGS)]] = Field(
        default=None,
        description="For mode='direct': the flow slug this request is asking for. For mode='insight': "
        "the flow slug ONLY if the message itself clearly names/implies one (e.g. mentions 'winback' by "
        "name) - otherwise null, so the caller investigates first and picks the best-fitting flow. "
        "Null if it can't be confidently determined either way - do not guess.",
    )
    signal_question: Optional[str] = Field(
        default=None,
        description="For mode='insight' only: the business signal/problem/question to investigate, "
        "as its own clean sentence (e.g. 'Is OTC serum revenue declining?'). Null for mode='direct'.",
    )


def parse_email_request(text: str) -> dict:
    """Turn a free-text request (e.g. from a Slack mention - 'write a P1
    email for Marcus', 'make one for someone who abandoned their cart, his
    name's Wei', or 'OTC serum sales are down, write something to fix it for
    Wei') into either a direct flow+name request or an insight-driven
    business signal to investigate first, by matching against the real flow
    catalog. Any field can be None if the request didn't make it clear, so
    the caller can ask for clarification instead of guessing."""
    llm = get_llm("COPYWRITER")
    structured_llm = llm.with_structured_output(EmailIntent)

    catalog = "\n".join(
        f'- "{slug}": {flow["label"]} ({flow["track"]} track) - {flow["audience"]}'
        for slug, flow in FLOW_BY_SLUG.items()
    )
    system_text = (
        "You classify a free-text CRM email request against this real andSons flow catalog "
        "(slug: label - who it's for):\n" + catalog + "\n\n"
        "First decide the mode (see field description): a plain 'write me the <flow> email' request is "
        "'direct'. A request that describes a problem, a metric moving the wrong way, or asks the agent "
        "to figure out what to write based on what's happening is 'insight' - the flow isn't picked from "
        "wording alone in that case, it's investigated. This includes requests that name a specific flow "
        "but ALSO explicitly ask for it to be grounded in real data (mentions MoEngage, BigQuery, a "
        "dashboard, 'live data', 'right now') - naming a flow doesn't make it 'direct' if the message is "
        "also explicitly asking for a real data check first; that check would be silently skipped "
        "otherwise, which ignores what was actually asked for. "
        "There is no real customer in this conversation - never look for or expect a customer name; "
        "every draft always addresses a generic 'NAME' placeholder, so ignore names entirely when "
        "classifying. For 'direct' mode, leave flow_name null rather than guessing if it doesn't clearly "
        "map to one of these flows - do not default to the first flow in the list. For 'insight' mode, "
        "only fill flow_name if the message itself names/clearly implies a specific flow; otherwise leave "
        "it null."
    )
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", text)])
    chain = prompt | structured_llm
    try:
        result: EmailIntent = chain.invoke({})
    except Exception:
        # Real failure mode, not hypothetical: a message that isn't an email
        # request at all (e.g. "tell me what flows are live") can make the
        # underlying model try to answer in plain text instead of calling
        # the required structured-output tool - the API then rejects that
        # outright, and the raw error would otherwise leak straight into
        # Slack. mode="unclear" lets the caller give a clean, on-brand
        # clarification instead of an API error dump.
        logger.warning("parse_email_request: model failed to return structured output for %r", text)
        return {"mode": "unclear", "flow_name": None, "signal_question": None}
    return {
        "mode": result.mode,
        "flow_name": result.flow_name,
        "signal_question": result.signal_question,
    }


class FlowPick(BaseModel):
    flow_name: Optional[Literal[tuple(VALID_FLOW_SLUGS)]] = Field(
        default=None,
        description="The single best-fitting flow slug for this investigated business signal, or null "
        "if none of the real flows genuinely fit - do not force a pick.",
    )


def pick_flow_for_signal(question: str, brief_text: str) -> Optional[str]:
    """After investigate() has actually looked at the data, pick which real
    andSons flow this email should be framed as (its audience/goal/track/CTA
    still come from flows.py - the insight brief only changes emphasis, not
    which lifecycle moment the email represents). Returns None if nothing
    genuinely fits, so the caller can ask a person instead of forcing one."""
    llm = get_llm("COPYWRITER")
    structured_llm = llm.with_structured_output(FlowPick)

    catalog = "\n".join(
        f'- "{slug}": {flow["label"]} ({flow["track"]} track) - {flow["audience"]} Goal: {flow["goal"]}'
        for slug, flow in FLOW_BY_SLUG.items()
    )
    system_text = (
        "Given this investigated business signal and the real andSons flow catalog below, pick the ONE "
        "flow whose real trigger/audience/goal genuinely fits sending this email right now. Do not pick "
        "one just because it's topically related - the flow's actual trigger condition should plausibly "
        "apply. Leave flow_name null if nothing genuinely fits rather than forcing a weak match.\n\n"
        + catalog
    )
    human_text = f"Signal: {question}\n\nInvestigation findings:\n{brief_text}"
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm
    try:
        result: FlowPick = chain.invoke({})
    except Exception:
        # Same real failure mode as parse_email_request() - the underlying
        # model can refuse structured output entirely on an odd input. The
        # caller already treats a null flow_name as "ask a person which flow
        # to use", so failing safe to None here (never raising up into the
        # Slack handler as a raw API error) is the same graceful path.
        logger.warning("pick_flow_for_signal: model failed to return structured output for %r", question)
        return None
    return result.flow_name

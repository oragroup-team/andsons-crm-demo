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

from categories import DEFAULT_CATEGORY, VALID_CATEGORY_SLUGS, category_notes_text
from flows import FLOW_BY_SLUG, VALID_FLOW_SLUGS
from image_bank import HERO_BANK, HERO_KEYS, hero_bank_for_category
from text_sanitize import sanitize_text

from .creative_director_agent import direct_touchpoint
from .learned_rules_agent import learned_rules_text
from .llm_provider import get_llm, invoke_with_retry

logger = logging.getLogger("copywriter_agent")

# get_llm() defaults to temperature=0.0 (right for classification calls
# like parse_email_request/pick_flow_for_signal below, where the same
# answer every time is correct). Actual copywriting is the opposite case -
# confirmed live: at 0.0 the model converges hard toward reproducing the
# golden reference almost verbatim (two separate runs of the same P1 email
# came back as near-paraphrases of each other and of the golden example),
# which is exactly what reads as "copying a template" instead of writing
# fresh copy. 0.8 is used at every real content-generation call site below
# (never the classification ones) - high enough for genuinely varied
# writing, not so high it drifts off-brand or off-compliance (the Sweeper
# still catches anything that does).
_CREATIVE_TEMPERATURE = 0.8

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

# The WhatsApp CS line and Unsubscribe are never optional (Unsubscribe in
# particular is close to universally a legal requirement). The address line
# is the one human-toggleable exception - see EmailContent.include_address.
_WHATSAPP_CS_LINE = "WhatsApp customer service: https://api.whatsapp.com/message/VX2SIFBLE7ECI1?autoload=1&app_absent=0"
_ADDRESS_LINE = "andSons Pte. Ltd., 1 Fusionopolis Place, #17-10, Galaxis, Singapore 138522"
_UNSUBSCRIBE_LINE = "Unsubscribe"


def _footer_lines(include_address: bool = True) -> List[str]:
    lines = [_WHATSAPP_CS_LINE]
    if include_address:
        lines.append(_ADDRESS_LINE)
    lines.append(_UNSUBSCRIBE_LINE)
    return lines

SYSTEM_PROMPT = """You are the senior CRM copywriter for andSons, a 100% online men's health telehealth \
brand in Singapore (licensed doctors, discreet delivery). You write in the Juniper/Eucalyptus register: \
calm, direct, medically literate, reassuring, warm, personal, plainspoken. Short one-line paragraphs with \
breathing room. Talks to ONE person, never a segment.

Use this approved P1 golden template ONLY as a register and restraint reference - tone, sentence length, \
how much warmth, how much is left unsaid. It is NOT a script: never reuse its specific sentence shapes, \
opening move, or phrases (e.g. "X has been finalised", "you've already taken the first step", "it only \
takes a minute", "we'll ship it discreetly") even when writing this exact flow/touchpoint - a human \
copywriter asked to write ten emails at this standard would find ten different real sentences, not ten \
palette-swapped copies of one template. If your draft's opening line or structure would look like a \
paraphrase of the golden example sitting next to it, rewrite it from a genuinely different angle instead:

---
{golden_reference}
---
(Note: that real approved template predates this system using a literal "NAME" placeholder - its "[name]" \
is the same idea, not a different, still-required token. Always address the customer as NAME, never write \
"[name]" yourself.)

LANGUAGE RULES (absolute, every flow):
- British English spelling everywhere (personalised, customised, recognise, colour, programme) - never \
American spelling.
- NEVER use em-dashes or long dashes as punctuation. Use full stops or commas instead.
- No exclamation marks. Sentence case (not Title Case).
- No app-speak: never "activate", "tap", "unlock". A plan is "confirmed" or "reviewed", never "activated".
- Signature is always "The andSons team" with no dash before it. Do not write a footer, unsubscribe line, \
or address yourself - those are appended automatically after your copy, based on the separate \
include_address field below (see its own description) for the address line specifically.
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
seals, deadlines, or features. NEVER state a specific number, percentage, or clinical statistic about \
condition prevalence, treatment efficacy, or outcomes anywhere in the copy (e.g. "X% of men", "affects 1 \
in Y") - there is no verified figure available to you in this system, so any such number you write would \
be invented, regardless of how plausible it sounds or what citation you attach to it. The DOI footnote \
(DOI: 10.1111/dth.12246) and "Individual results vary." exist ONLY to accompany a specific approved \
statistic handed to you explicitly elsewhere in this prompt (e.g. in the Head of CRM brief) - never write \
that footnote, or any DOI-shaped citation, on a number you came up with yourself, AND never write it on a \
category other than Hair Loss regardless (that specific approved statistic is Hair-Loss-specific, never \
transplant it to a different category's email). When you want to make the same point without a real \
number, say it as a plain, ungraded truth instead, genuinely relevant to THIS email's category (see the \
category context below) rather than defaulting to a hair-loss-specific example out of habit.
- No cure/guarantee language ("cure it", "guaranteed results", "100% works"). No shame or fear-based \
pressure. No fake urgency or countdown framing - the only legitimate motivator is a real, calmly-stated \
truth genuinely relevant to THIS email's category (see the category context below for what that is here - \
for Hair Loss specifically, the real fact is that it's a progressive condition, so starting early protects \
more of what he still has; that specific fact belongs to Hair Loss only, never write it for a different \
category).
- This email may send the same day as the underlying trigger event (e.g. a consultation or quiz). Never \
imply days have passed or that the customer has been deliberating ("taking a few days", "you've been \
thinking it over") unless the flow brief says otherwise.

STRUCTURE (per email, use judgement - these are OPTIONAL composable blocks, not a fixed template):
- opening_lines: 2-3 short warm paragraphs, empathy before logistics - acknowledge the step he's already \
taken before pointing at the next one.
- what_happens_next (OPTIONAL): a short numbered block (2-3 short lines) ONLY when it genuinely adds \
clarity for this moment. Omit it (leave null) for a simpler, more personal email - restraint is the \
luxury here, not decoration. Its markers (icon or number) default automatically - if a reviewer's \
feedback specifically asks for a different marker look (e.g. plain bullets), that is the step_marker_style \
field's job, not something to solve by editing the step text - use that field.
- trust_line (OPTIONAL): a thin centred line like "Doctor-led plan · Clinically studied · Discreet \
delivery", plain text separated by " · ", never a description of badge graphics. Include only when it \
adds confidence; omit for a simpler email.
- gentle_truth_line (OPTIONAL): one gentle, caring true statement relevant to THIS EMAIL'S ACTUAL \
CATEGORY (see the category context below) and this flow's moment, stated as care not urgency - e.g. for \
Hair Loss specifically, the real progressive-condition truth; for a different category, a real truth that \
actually belongs to THAT category's own real objections/psychology, never the hair-loss one carried over \
by default. Omit if nothing true and relevant fits - omitting this is always safer than reaching for the \
wrong category's truth.
- Never more than one of each block. Never fabricate content to fill a block - an empty/omitted block is \
always better than an invented one.

BACKGROUND COLOUR (rare, reactive only): defaults to null - the standard brand background. Only set \
background_color when a human reviewer's feedback specifically asks for a different background colour for \
the email; set it to the real hex code that genuinely represents what they described. This is a real, \
renderable field - never write a note saying you can't change the background, and never describe the \
change in the copy text instead of setting the field.

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

LEARNED RULES (standing requirements distilled from real past human feedback on real drafts; apply every \
one before the Sweeper has to catch it):
{learned_rules}

{category_notes}

FLOW FOR THIS EMAIL: {flow_name}
{flow_brief}

Address the customer by first name: {first_name}.
"""


def _build_hero_catalog(category: str = DEFAULT_CATEGORY) -> str:
    """Render the hero bank FOR THIS CATEGORY ONLY - key, what the photo
    actually shows, and the moment it's built for - so the Copywriter
    chooses based on real content instead of guessing from a bare key name
    (that gap was why it kept defaulting to the same one or two
    'safe-sounding' keys). Category-scoped via hero_bank_for_category() -
    a real, live-caught gap this fixes: every photo in the original bank
    is Hair-Loss-specific (a hand in someone's hair, a Redensyl bottle),
    so showing the FULL bank on an ED/PE/Weight-Loss/Skin email would put
    an actively wrong image in front of the model as a real option."""
    bank = hero_bank_for_category(category)
    lines = []
    for key, entry in bank.items():
        locked = " [LOCKED - headline already baked in, do not add hero_headline]" if entry["baked_headline"] else ""
        lines.append(f'- "{key}"{locked}: {entry["description"]}. Best for: {entry["moment"]}.')
    if not lines:
        return "(No reviewed photos exist for this category yet - always choose \"none\" (text-first).)"
    return "\n".join(lines)


def _build_flow_brief(flow_slug: str, category: str = DEFAULT_CATEGORY) -> str:
    flow = FLOW_BY_SLUG.get(flow_slug)
    if flow is None:
        flow = FLOW_BY_SLUG["p1_plan_not_purchased"]

    lines = [
        f"Flow: {flow['label']} (track: {flow['track']}).",
        f"Reader: {flow['audience']}",
        f"Goal: {flow['goal']}",
        f"Suggested CTA for this flow: something like \"{flow['cta']}\".",
    ]
    # Real, live-caught bug this fixes (2026-08-25): flows.py's Reader/Goal
    # text above (and each touchpoint's own "intent" line) was written for
    # Hair Loss specifically and genuinely names it in several flows (e.g.
    # "finished the hair loss assessment quiz") - handing that straight to
    # the model with no instruction produced exactly what you'd expect: a
    # Sexual Health/Weight Loss/Skin email that talks about hair loss,
    # confirmed live across repeated runs. This instruction is the fix,
    # not a cosmetic note - it must survive for every category ≠ hair_loss.
    if category != "hair_loss":
        lines.append(
            "CRITICAL - THE READER/GOAL TEXT ABOVE, AND THIS FLOW'S TOUCHPOINT 'MOMENT' TEXT BELOW, WERE "
            "WRITTEN FOR HAIR LOSS: this email's real category is different (see the category context "
            "below) - translate the SITUATION (which lifecycle moment this is: a plan awaiting payment, a "
            "missed consult, an abandoned cart, and so on) to this category's own real equivalent, using "
            "the category context's actual voice/objections. Never literally repeat a hair-loss-specific "
            "word or phrase from the text above (\"hair loss\", \"hairline\", \"assessment quiz\" if it "
            "names hair loss specifically, a hair product name) in your actual output - if the Reader/Goal "
            "text names hair loss specifically, treat that as this category's OWN version of that same "
            "moment (e.g. 'finished the assessment' becomes this category's own real intake/assessment "
            "step, not a hair loss one)."
        )
    # Real, deliberate constraint, not an oversight: the approved OTC
    # catalogue below (Redensyl/Trio/Kit) is real and verified ONLY for
    # Hair Loss - no other category has a real, verified product/price in
    # this system (see categories.py's otc_verified flag). Naming a
    # plausible-sounding product/price for another category would be an
    # invention, which this system's own "never invent" rule forbids -
    # so allow_price is honoured only for category == "hair_loss";
    # every other category defaults to no-price/consult framing
    # regardless of what this specific flow's allow_price flag says.
    if flow["allow_price"] and category == "hair_loss":
        lines.append(
            "You MAY reference a real product and its real price if it strengthens this message - "
            "the approved OTC catalogue is: 3% Redensyl Anti-Hair Loss Serum ($42), Intense Hair Growth "
            "Trio ($78), Intense Hair Growth Kit ($78). Never invent a different price or product."
        )
    elif flow["allow_price"]:
        lines.append(
            "HARD CONSTRAINT: this flow normally allows a product/price mention, but there is NO real, "
            "verified OTC product or price for this category in this system - naming one would be "
            "invented. Never mention a price, a dollar amount, a discount code, or any specific product "
            "name for this category. Speak to the doctor-led plan/consultation instead."
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
    step_marker_style: Optional[str] = Field(
        default=None,
        description="Leave null for the normal default (a content-matched icon, or a plain numbered "
        "circle when no icon genuinely fits). Only set this when a human reviewer's feedback specifically "
        "asks for a different visual treatment of the what_happens_next markers - describe, in a few of "
        "your own plain words, the actual visual thing they asked for (e.g. 'plain round bullet dots, no "
        "numbers or icons'). This is read by the renderer, not shown to the customer - describe what you "
        "genuinely understood them to want, don't pick from a fixed list of options.",
    )
    gentle_truth_line: Optional[str] = Field(
        default=None,
        description="OPTIONAL: one gentle, caring true sentence relevant to this flow's moment (care, "
        "not urgency). Omit if nothing true and relevant fits this flow.",
    )
    cta_text: str = Field(description="The call-to-action button text, e.g. 'Start My Treatment'")
    cta_position: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Where the CTA button actually sits along the row, as a fraction of the space "
        "between the two margins: 0.0 means its left edge sits flush against the left margin, 1.0 "
        "means its right edge sits flush against the right margin - that is the entire meaning of the "
        "number, a plain physical coordinate, nothing else. Default 0.0 (the golden template). When a "
        "human reviewer gives feedback about where the button should sit, read what they actually "
        "wrote, picture the real button actually moving on the real page you're looking at, and set "
        "the number that puts it where a person reading their words would expect to see it land - "
        "your own genuine read of their intent, not a memorised mapping from stock phrases to numbers.",
    )
    trust_line: Optional[str] = Field(
        default=None,
        description="OPTIONAL: short centred trust line, plain text separated by ' · ', e.g. "
        "'Doctor-led plan · Clinically studied · Discreet delivery'. Omit for a simpler email.",
    )
    background_color: Optional[str] = Field(
        default=None,
        description="Leave null for the normal default (the standard brand background). Only set this "
        "when a human reviewer's feedback specifically asks for a different background colour - a real "
        "hex colour code (e.g. '#d6e9f7') that is your own genuine read of the colour they actually "
        "described, not a memorised mapping from stock phrases to hex codes. This is the whole page's "
        "background - the email template's own real, brand-owned canvas, not any third-party app's UI.",
    )
    body_style: Literal["normal", "italic"] = Field(
        default="normal",
        description="Leave 'normal' by default. Set to 'italic' ONLY when a human reviewer's feedback "
        "explicitly asks for italic/slanted text styling on the email's body copy (the greeting, opening "
        "lines, and gentle truth line - not the CTA button, the what_happens_next list, or the footer, "
        "which each keep their own look regardless of this field). This is a real, renderable field - "
        "never write a `note` saying italic isn't supported, just set this directly.",
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
    include_address: bool = Field(
        default=True,
        description="Whether the footer includes the registered business address line (\"andSons Pte. "
        "Ltd., 1 Fusionopolis Place...\"). This is very likely a legal requirement for commercial email "
        "in most jurisdictions - default true, and do NOT set it false on your own initiative. Set to "
        "false ONLY when a human reviewer's feedback explicitly asks to remove/drop/hide the address; "
        "set back to true if a later round asks to add it back. The WhatsApp customer-service line and "
        "the Unsubscribe line are NEVER optional regardless of this field - only the address line toggles.",
    )
    note: Optional[str] = Field(
        default=None,
        description="Leave null on a normal draft, and leave null unless the CURRENT, newest feedback you "
        "were just given (not any earlier round already applied in a prior draft) asks for something a "
        "hard rule (compliance, language rules, the flow's exact CTA label, invent-nothing) does not "
        "allow: apply the closest compliant interpretation of what they actually asked for instead of just "
        "refusing, and use this field to say in one short, plain sentence what they asked for and why you "
        "couldn't do it literally. This field must NEVER fire just because a requested visual or content "
        "change has no dedicated field of its own below - an aesthetic or content request is not a hard "
        "rule conflict, it's a real, buildable change: check first whether an existing field (background_"
        "color, cta_position, step_marker_style, include_address, body_style, or a plain edit to any text "
        "field) can "
        "genuinely represent what they asked for, and if one can, set it directly - never write a note "
        "about something you could have just done. Never use this field to comment on, re-explain, or "
        "re-litigate a past feedback round - those are already settled.",
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
            "note": sanitize_text(content.note) if content.note else None,
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
            # Plain hyphen, never an em-dash - this internal annotation is
            # part of the same candidate text the Sweeper reads, and the
            # brand's em-dash ban applies to anything in that text, not
            # just what a customer would actually see. A real, latent bug
            # this whole session: this line always used an em-dash, but it
            # only started reliably failing every touchpoint once the real
            # (properly strict) Anthropic Sweeper replaced the Groq
            # fallback that had been silently letting it slide.
            headline_part = f' - {label}: "{headline}"'
        else:
            headline_part = ""
        parts.append(f'[HERO IMAGE: {description}{headline_part}]')
        parts.append("")

    # Real gap this closes (found 2026-08-26, adding body_style below):
    # none of the "sticky" styling fields below show up ANYWHERE in this
    # plain-text render, which is the ONLY form of the previous draft a
    # later, unrelated revision round actually sees (previous_draft is
    # rendered_text, not the structured EmailContent) - so a background
    # colour, repositioned CTA, custom step markers, hidden address, or
    # italic body a reviewer asked for in one round had no way to survive
    # into the next round's context, and a later edit could silently
    # revert it without anyone asking for that. Only non-default values are
    # listed, same principle as the HERO IMAGE annotation above: surface
    # state that isn't otherwise visible in flowing prose.
    style_notes = []
    if content.background_color:
        style_notes.append(f"background colour {content.background_color}")
    if content.cta_position:
        style_notes.append(f"CTA button at horizontal position {content.cta_position:.2f} (0.0=left, 1.0=right)")
    if content.step_marker_style:
        style_notes.append(f"step markers: {content.step_marker_style}")
    if not content.include_address:
        style_notes.append("address line hidden")
    if content.body_style == "italic":
        style_notes.append("body copy styled italic")
    if style_notes:
        parts.append(f"[CURRENT STYLING - already applied, keep unless this round's feedback changes it: {'; '.join(style_notes)}]")
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

    parts += ["", "The andSons team", ""] + _footer_lines(content.include_address)
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
claim; the ban on inventing a hair-loss percentage/statistic above applies regardless of anything in here.

{brief}
"""


def generate_email(
    flow_name: str,
    first_name: str,
    correction: Optional[str] = None,
    insight_brief: Optional[str] = None,
    category: str = DEFAULT_CATEGORY,
) -> dict:
    """Run the Copywriter agent. If `correction` is provided, it is appended
    to the ORIGINAL system prompt/constraints (never sent alone) so the model
    keeps the full brand context on every retry. If `insight_brief` is
    provided (from agents.insight_agent), it's included as internal strategy
    context only - see _INSIGHT_BRIEF_INSTRUCTION for the leak-prevention
    rules enforced around it. `category` (one of categories.VALID_CATEGORY_
    SLUGS) selects the reader psychology/objections/compliance context and
    the category-scoped hero photo catalogue - defaults to hair_loss, the
    original and only category this system supported before 2026-08-25."""
    llm = get_llm("COPYWRITER", temperature=_CREATIVE_TEMPERATURE)
    structured_llm = llm.with_structured_output(EmailContent)

    flow_brief = _build_flow_brief(flow_name, category)
    system_text = SYSTEM_PROMPT.format(
        golden_reference=GOLDEN_P1_REFERENCE,
        flow_name=flow_name,
        flow_brief=flow_brief,
        first_name=first_name,
        hero_catalog=_build_hero_catalog(category),
        learned_rules=learned_rules_text(),
        category_notes=category_notes_text(category),
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

Use this approved P1 style reference for register only (tone, warmth, restraint - not content, not \
sentence shapes, not phrases - that email is a different touchpoint entirely):
---
{golden_reference}
---
(Note: that real approved template predates this system using a literal "NAME" placeholder - its "[name]" \
is the same idea, not a different, still-required token. Always address the customer as NAME.)

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

LEARNED RULES (standing requirements distilled from real past human feedback; apply every one before the \
Sweeper has to catch it):
{learned_rules}

{category_notes}

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
    note: Optional[str] = Field(
        default=None,
        description="Leave null on a normal draft, and leave null unless the CURRENT, newest feedback you "
        "were just given (not any earlier round already applied in a prior draft) asks for something a "
        "hard rule does not allow: apply the closest compliant interpretation instead of just refusing, "
        "and say in one short, plain sentence what they asked for and why you couldn't do it literally. "
        "This field must NEVER fire just because a change has no dedicated field of its own - most content "
        "requests (the wording, the hook, the CTA label) are just a plain edit to an existing text field, "
        "so make it directly instead of writing a note. The one genuine hard constraint specific to this "
        "channel: this mockup renders WhatsApp's own real, actual dark-mode app chrome (colours, bubble "
        "style), not an andSons-owned design - a request to restyle THAT (not the message content itself) "
        "is the one case where a note explaining that real constraint is correct, not a missing field. "
        "Never use this field to comment on, re-explain, or re-litigate a past feedback round - those are "
        "already settled.",
    )


def _sanitize_whatsapp(content: WhatsAppContent) -> WhatsAppContent:
    return content.model_copy(
        update={
            "link_title": sanitize_text(content.link_title),
            "hook_line": sanitize_text(content.hook_line),
            "body": sanitize_text(content.body),
            "cta_text": sanitize_text(content.cta_text),
            "note": sanitize_text(content.note) if content.note else None,
        }
    )


def render_whatsapp(content: WhatsAppContent) -> str:
    """Plain-text rendering of a WhatsApp touchpoint, in the same
    Sweeper-readable shape as render_email() - the link-preview title, the
    bold hook line, the rest of the message, then its one CTA button line,
    never HTML."""
    return f"[LINK PREVIEW: {content.link_title}]\n\n{content.hook_line}\n{content.body}\n\n[{content.cta_text}]"


PUSH_SYSTEM_PROMPT = """You are the senior CRM copywriter for andSons, a 100% online men's health telehealth \
brand in Singapore. You are writing ONE push notification touchpoint in a real multi-step andSons \
lifecycle flow - not an email, not a WhatsApp message. A push notification is the shortest touchpoint \
that exists: it appears on the customer's phone lock screen/notification shade as a title line and a body \
line, nothing else. No greeting, no signature, no HTML, no link pasted into the text, no CTA button - \
tapping the notification itself is the only action, so there is nothing else to write.

Use this approved P1 style reference for register only (tone, warmth, restraint - never content, never \
sentence shapes, never phrases - a push notification looks nothing like this email):
---
{golden_reference}
---

LANGUAGE RULES (absolute): British English spelling everywhere (personalised, customised, recognise, \
colour, programme) - never American spelling. NEVER use em-dashes or long dashes as punctuation - use \
full stops or commas. No exclamation marks. Sentence case. No app-speak ("activate", "tap", "unlock").

COMPLIANCE (hard rules): NEVER name a prescription medicine anywhere. Treatment decisions belong to the \
doctor, never the brand. NEVER claim the customer can message the doctor directly. Brand name is exactly \
"andSons". Invent nothing: no fabricated stats, social proof, counters, badges, deadlines. No \
cure/guarantee language, no shame, no fake urgency.
{price_rule}

NO DEFENSIVE META-COMMENTARY. PAYMENT-FRAMING BAN: never lead with money.

LEARNED RULES (standing requirements distilled from real past human feedback; apply every one before the \
Sweeper has to catch it):
{learned_rules}

{category_notes}

FLOW: {flow_name}
{flow_brief}

THIS TOUCHPOINT'S MOMENT IN THE FLOW (timing: {timing}): {intent}

{prior_context}

Address the customer as NAME where it's natural to name him at all - a push notification is short enough \
that many good ones don't need to.
"""


class PushContent(BaseModel):
    title: str = Field(
        description="The notification's title line - short and punchy, under about 40 characters so it "
        "is not cut off on a phone lock screen. A headline, not a greeting - never starts with 'Hi NAME'."
    )
    body: str = Field(
        description="The notification's body line - ONE short sentence, under about 90 characters, shown "
        "below the title. Can address NAME directly here if it reads naturally."
    )
    note: Optional[str] = Field(
        default=None,
        description="Leave null on a normal draft, and leave null unless the CURRENT, newest feedback you "
        "were just given (not any earlier round already applied in a prior draft) asks for something a "
        "hard rule does not allow: apply the closest compliant interpretation instead of just refusing, "
        "and say in one short, plain sentence what they asked for and why you couldn't do it literally. "
        "This field must NEVER fire just because a change has no dedicated field of its own - a wording "
        "request is just a plain edit to title/body, so make it directly instead of writing a note. The "
        "one genuine hard constraint specific to this channel: this mockup renders a real phone lock "
        "screen's own actual chrome (wallpaper, clock, card style) - andSons doesn't control what a "
        "customer's phone OS looks like in reality, so a request to restyle THAT (not the notification's "
        "own title/body text) is the one case where a note explaining that real constraint is correct, not "
        "a missing field. Never use this field to comment on, re-explain, or re-litigate a past feedback "
        "round - those are already settled.",
    )


def _sanitize_push(content: PushContent) -> PushContent:
    return content.model_copy(
        update={
            "title": sanitize_text(content.title),
            "body": sanitize_text(content.body),
            "note": sanitize_text(content.note) if content.note else None,
        }
    )


def render_push(content: PushContent) -> str:
    """Plain-text rendering of a push touchpoint, in the same
    Sweeper-readable shape as render_email()/render_whatsapp() - title
    then body, nothing else (no CTA/link - a push notification has none)."""
    return f"{content.title}\n{content.body}"


def _prior_touchpoints_context(prior: list) -> str:
    if not prior:
        return "This is the FIRST touchpoint in the flow - nothing has been sent yet."
    used_heroes = [p["hero"] for p in prior if p.get("hero") and p["hero"] != "none"]
    lines = [
        "EARLIER TOUCHPOINTS ALREADY SENT in this same flow, so this one must feel like the next step in "
        "one continuous conversation, never a repeat of an earlier one. Read what's already been said "
        "below and pick a genuinely different rhetorical entry point than every one of them - if an "
        "earlier touchpoint opened by restating the plan/offer, this one should open somewhere else "
        "entirely (a specific detail, a direct question, what happens after he acts, a different piece of "
        "reassurance) - not the same idea in different words:"
    ]
    for p in prior:
        lines.append(f"- Touchpoint {p['n']} ({p['channel']}, {p['timing']}): {p['summary']}")
    if used_heroes:
        lines.append(
            f"Heroes already used in this flow: {', '.join(used_heroes)} - NEVER reuse any of these; "
            "pick a different bank photo or 'none'."
        )
    return "\n".join(lines)


def generate_touchpoint(
    flow_name: str, step: dict, prior_summaries: list, correction: Optional[str] = None,
    insight_brief: Optional[str] = None, category: str = DEFAULT_CATEGORY,
) -> dict:
    """Single dispatch point for "generate one touchpoint of whatever
    channel this step is" - used by generate_flow() below and by
    feedback_node.py's Sweeper-correction loop and revise_flow_touchpoint(),
    so all three stay in sync as channels are added instead of each
    hand-rolling its own if/elif channel dispatch."""
    if step["channel"] == "email":
        return generate_flow_email_touchpoint(flow_name, step, prior_summaries, correction=correction, insight_brief=insight_brief, category=category)
    if step["channel"] == "whatsapp":
        return generate_flow_whatsapp_touchpoint(flow_name, step, prior_summaries, correction=correction, insight_brief=insight_brief, category=category)
    if step["channel"] == "push":
        return generate_flow_push_touchpoint(flow_name, step, prior_summaries, correction=correction, insight_brief=insight_brief, category=category)
    raise ValueError(f"Unknown channel: {step['channel']!r}")


def generate_flow(
    flow_name: str, insight_brief: Optional[str] = None, cadence: Optional[list] = None,
    category: str = DEFAULT_CATEGORY,
) -> dict:
    """Generate every real touchpoint in a flow's cadence, in order, each
    aware of what earlier touchpoints in the same flow already said (so
    the sequence reads as one continuous journey, and no hero image or
    opening line repeats). Always addresses the NAME placeholder - there
    is no real customer in a Slack conversation to name. Returns
    {"flow_name", "touchpoints": [...]} - each touchpoint has "channel",
    "timing", "intent", "rendered_text", "content", and (email only) hero
    fields, ready for the Sweeper and the image renderers.

    `cadence`, when given, is the Head of CRM's actual decided touchpoint
    plan (head_of_crm_agent.brief_campaign()'s "cadence") - flows.py's own
    cadence is a baseline reference, not a hardcoded requirement; the real
    decision belongs to whichever brief actually grounded this generation.
    Falls back to the flow's own baseline cadence only when no decided
    cadence was given at all (e.g. a direct call bypassing the Head of CRM
    step, such as a test)."""
    flow = FLOW_BY_SLUG.get(flow_name)
    if flow is None:
        raise ValueError(f"Unknown flow: {flow_name}")

    touchpoints = []
    prior_summaries = []

    for step in (cadence or flow["cadence"]):
        try:
            touchpoint = generate_touchpoint(flow_name, step, prior_summaries, insight_brief=insight_brief, category=category)
        except RuntimeError as exc:
            # Real failure mode, not hypothetical: even with a 5-attempt
            # retry (invoke_with_retry), a single touchpoint can still
            # exhaust it (caught live - a Groq forced-tool-call rejection,
            # 3/3 at the time, since raised to 5). Before this, one bad
            # touchpoint took the ENTIRE flow down with it - a 5-touchpoint
            # flow that got 4 perfectly good touchpoints still surfaced as
            # one hard failure to the Slack user, and there was no way to
            # retry just the one that failed. A clearly-marked placeholder
            # keeps the rest of the flow posting normally; the caller
            # (feedback_node.run_flow_pipeline) skips the Sweeper for it,
            # and app.py posts a plain "reply to retry" message instead of
            # an image - reusing the same per-touchpoint revision path
            # (revise_flow_touchpoint) already built for editing a
            # touchpoint on request, so retrying this one is a normal
            # reply, not a special case.
            logger.error("Touchpoint %d (%s) failed to generate after retrying: %s", step["n"], step["channel"], exc)
            touchpoint = {
                "n": step["n"],
                "channel": step["channel"],
                "timing": step["timing"],
                "intent": step["intent"],
                "content": None,
                "rendered_text": None,
                "hero": None,
                "generation_failed": True,
            }
        touchpoints.append(touchpoint)
        prior_summaries.append(_touchpoint_summary(touchpoint))

    return {"flow_name": flow_name, "touchpoints": touchpoints}


def _touchpoint_summary(touchpoint: dict) -> dict:
    if touchpoint.get("generation_failed"):
        return {
            "n": touchpoint["n"],
            "channel": touchpoint["channel"],
            "timing": touchpoint["timing"],
            "hero": None,
            "summary": "(this step failed to generate and needs a manual retry - nothing was actually sent)",
        }
    content = touchpoint["content"]
    if touchpoint["channel"] == "email":
        summary = f"Subject '{content['subject']}' - {content['opening_lines'][0] if content.get('opening_lines') else ''}"
    elif touchpoint["channel"] == "whatsapp":
        summary = f"{content['hook_line']} {content['body']}"
    else:  # push
        summary = f"{content['title']} - {content['body']}"
    return {
        "n": touchpoint["n"],
        "channel": touchpoint["channel"],
        "timing": touchpoint["timing"],
        "hero": touchpoint["hero"],
        "summary": summary,
    }


def generate_flow_email_touchpoint(
    flow_name: str, step: dict, prior_summaries: list, correction: Optional[str] = None,
    insight_brief: Optional[str] = None, category: str = DEFAULT_CATEGORY,
) -> dict:
    """Generate (or regenerate, with `correction`) ONE email touchpoint of a
    flow's real cadence - shares the exact same prompt machinery as
    generate_email(), plus this touchpoint's real timing/intent and what
    earlier touchpoints in the same flow already said (so a Sweeper-driven
    retry stays aware of the rest of the sequence, not just its own text)."""
    flow = FLOW_BY_SLUG[flow_name]
    flow_brief = _build_flow_brief(flow_name, category)
    llm = get_llm("COPYWRITER", temperature=_CREATIVE_TEMPERATURE)
    structured_llm = llm.with_structured_output(EmailContent)
    system_text = SYSTEM_PROMPT.format(
        golden_reference=GOLDEN_P1_REFERENCE,
        flow_name=flow_name,
        flow_brief=flow_brief,
        first_name="NAME",
        hero_catalog=_build_hero_catalog(category),
        learned_rules=learned_rules_text(),
        category_notes=category_notes_text(category),
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

    # Email Creative Director: a genuine second, independent pass that
    # reviews (and can override) the Copywriter's own hero proposal -
    # never rewrites the copy itself. Real pipeline step (see
    # creative_director_agent.py's module docstring); its hero-uniqueness
    # check is a real second opinion, not just trusting the Copywriter's
    # own self-restraint against reusing a hero already used elsewhere in
    # this flow.
    direction = direct_touchpoint(content.model_dump(), prior_summaries, category=category)
    content = content.model_copy(update={
        "hero": direction["hero"],
        "hero_headline": direction["hero_headline"],
    })

    hero_info = resolve_hero(content)
    rendered = render_email(content, "NAME", hero_info=hero_info)
    result_content = content.model_dump()
    result_content.update(
        {
            "hero": hero_info["hero"],
            "hero_image_url": hero_info["hero_image_url"],
            "hero_headline": hero_info["hero_headline"],
            "hero_source": hero_info["hero_source"],
            "headline": direction.get("headline"),
            "art_rationale": direction.get("art_rationale"),
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
    insight_brief: Optional[str] = None, category: str = DEFAULT_CATEGORY,
) -> dict:
    """Generate (or regenerate, with `correction`) ONE WhatsApp touchpoint
    of a flow's real cadence. See generate_flow_email_touchpoint()."""
    flow = FLOW_BY_SLUG[flow_name]
    flow_brief = _build_flow_brief(flow_name, category)
    # Same category-gated logic as _build_flow_brief() - the real OTC
    # catalogue is verified for hair_loss only, see that function's own
    # comment for why every other category is treated as no-price
    # regardless of this flow's own allow_price flag.
    price_rule = (
        "" if flow["allow_price"] and category == "hair_loss" else
        "HARD CONSTRAINT: never mention a price, a dollar amount, or a discount code in this message."
    )
    llm = get_llm("COPYWRITER", temperature=_CREATIVE_TEMPERATURE)
    structured_llm = llm.with_structured_output(WhatsAppContent)
    system_text = WHATSAPP_SYSTEM_PROMPT.format(
        golden_reference=GOLDEN_P1_REFERENCE,
        flow_name=flow_name,
        flow_brief=flow_brief,
        timing=step["timing"],
        intent=step["intent"],
        price_rule=price_rule,
        prior_context=_prior_touchpoints_context(prior_summaries),
        learned_rules=learned_rules_text(),
        category_notes=category_notes_text(category),
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


def generate_flow_push_touchpoint(
    flow_name: str, step: dict, prior_summaries: list, correction: Optional[str] = None,
    insight_brief: Optional[str] = None, category: str = DEFAULT_CATEGORY,
) -> dict:
    """Generate (or regenerate, with `correction`) ONE push-notification
    touchpoint of a flow's real cadence. See generate_flow_email_touchpoint()."""
    flow = FLOW_BY_SLUG[flow_name]
    flow_brief = _build_flow_brief(flow_name, category)
    # Same category-gated logic as _build_flow_brief() - see that
    # function's comment for why non-hair_loss categories are always
    # no-price regardless of this flow's own allow_price flag.
    price_rule = (
        "" if flow["allow_price"] and category == "hair_loss" else
        "HARD CONSTRAINT: never mention a price, a dollar amount, or a discount code in this notification."
    )
    llm = get_llm("COPYWRITER", temperature=_CREATIVE_TEMPERATURE)
    structured_llm = llm.with_structured_output(PushContent)
    system_text = PUSH_SYSTEM_PROMPT.format(
        golden_reference=GOLDEN_P1_REFERENCE,
        flow_name=flow_name,
        flow_brief=flow_brief,
        timing=step["timing"],
        intent=step["intent"],
        price_rule=price_rule,
        prior_context=_prior_touchpoints_context(prior_summaries),
        learned_rules=learned_rules_text(),
        category_notes=category_notes_text(category),
    )
    if insight_brief:
        system_text += "\n\n" + _INSIGHT_BRIEF_INSTRUCTION.format(brief=insight_brief)
    human_text = f"Write touchpoint {step['n']} ({step['timing']}) of the {flow_name} flow, a push notification for NAME."
    if correction:
        human_text += (
            "\n\nThis is a REVISION. The previous draft of this touchpoint failed brand review. Keep "
            "every rule above in force and additionally apply this correction:\n" + correction
        )

    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm

    content, last_exc = invoke_with_retry(chain, label="Copywriter push touchpoint call")
    if content is None:
        raise RuntimeError(f"Copywriter failed to produce touchpoint {step['n']} ({last_exc}).") from last_exc

    content = _sanitize_push(content)
    rendered = render_push(content)
    return {
        "n": step["n"],
        "channel": "push",
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
    category: Literal[tuple(VALID_CATEGORY_SLUGS)] = Field(
        default=DEFAULT_CATEGORY,
        description="Which real andSons category this request is for: 'hair_loss', 'sexual_health' "
        "(covers both ED and PE), 'weight_loss', or 'skin'. Look for an explicit mention (e.g. 'a weight "
        "loss email', 'for an ED customer', 'sexual health flow') or a clear contextual signal (a "
        "product/condition named that only belongs to one category). Default to 'hair_loss' - the "
        "original category this system was built for - when the request gives no signal either way; do "
        "not guess a different category from weak evidence.",
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
        "it null. Also classify 'category' (see its own field description) - default to 'hair_loss' "
        "unless the request clearly signals a different one."
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
        return {"mode": "unclear", "flow_name": None, "signal_question": None, "category": DEFAULT_CATEGORY}
    return {
        "mode": result.mode,
        "flow_name": result.flow_name,
        "signal_question": result.signal_question,
        "category": result.category,
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

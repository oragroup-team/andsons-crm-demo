"""Copywriter agent - drafts OVA Singapore CRM touchpoints (email + WhatsApp).

EMAIL FORMAT: the team's real, stakeholder-confirmed template (a real,
full-length OVA "Mounjaro Program" onboarding email, reviewed in full) -
four fixed zones, per the stakeholder's own words: "Logo -> Hero banner &
CTA -> Body content -> Footer & Closing CTA". Zones 1/2/4 (logo, top hero+
CTA, closing footer+CTA) are FIXED and REQUIRED on every email. Zone 3
("body content") is DELIBERATELY FLEXIBLE - it's an ordered list of typed
content modules (see email_image_renderer.py's own docstring and this
file's SYSTEM_PROMPT for the full list: text, image_spotlight,
divider_header, icon_grid, two_column, callout_card, checklist_card,
price_card) that the Copywriter chooses and orders per email, matching how
the real reference email itself mixes several different module types
rather than repeating one fixed shape. This superseded an earlier, fully-
rigid version of this file (a single fixed callouts/features/price-card
shape) once the real stakeholder template was actually confirmed - see
git history if you need that context.

HERO IMAGES: a real, curated photo bank (image_bank.py, built from
`01_OVA_Rebrand_2025`), a per-email Copywriter judgement call, and an
independent Creative Director second opinion (creative_director_agent.py)
- selection is bank-only, never invented, and "none" is always a valid
choice when nothing genuinely fits - the exact same process the andSons
Email agent uses.

CALLOUTS/FEATURES are grounded in categories.py's real, approved per-
category service benefits (never invented) - see category_notes_text().

Voice = the four OVA tone-of-voice pillars: smart not stuffy, calm
authority, trustworthy not transactional, minimal and modern.

Structurally ported from the andSons backend (Pydantic structured output,
multi-touchpoint flow generation, the prior-context/sanitize machinery, the
hero-lock/Creative-Director pipeline, the WhatsApp WABA Call-To-Action
shape) - only the brand content, the email format, and the real OVA photo
bank differ.
"""
import logging
import re
from typing import Annotated, List, Literal, Optional, Union

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from categories import CATEGORIES, DEFAULT_CATEGORY, VALID_CATEGORY_SLUGS, category_notes_text
from hero_usage_tracker import record_hero_use, recent_heroes_prompt_block
from flows import FLOW_BY_SLUG, VALID_FLOW_SLUGS
from image_bank import HERO_BANK, HERO_KEYS, hero_bank_for_category
from text_sanitize import sanitize_text

from .creative_director_agent import direct_touchpoint
from .learned_rules_agent import learned_rules_text
from .llm_provider import get_llm, invoke_with_retry

logger = logging.getLogger("copywriter_agent")

_CREATIVE_TEMPERATURE = 0.8

_LEADING_NUMBER_RE = re.compile(r"^\s*\d+[.)]\s*")
_LEADING_GREETING_RE = re.compile(r"^\s*hi\s+[^\s,]+\s*,\s*", re.IGNORECASE)



def _strip_leading_number(step: str) -> str:
    return _LEADING_NUMBER_RE.sub("", step).strip()


def _strip_leading_greeting(line: str) -> str:
    return _LEADING_GREETING_RE.sub("", line).strip()


SYSTEM_PROMPT = """You are the senior CRM copywriter for OVA, a women's health telehealth service \
(OVA SINGAPORE - connects women with MOH-licensed doctors and pharmacies; OVA itself is not a clinic or \
pharmacy and does not prescribe or dispense).

THE FORMAT IS THE TEAM'S REAL, STAKEHOLDER-CONFIRMED TEMPLATE - four zones, in this fixed order, per the \
stakeholder's own words: "Logo -> Hero banner & CTA -> Body content -> Footer & Closing CTA":

1. LOGO - fixed chrome, not something you write.
2. HERO BANNER & CTA - REQUIRED on every email, no exceptions: a centred headline, one short subcopy \
sentence, a centred CTA button, and a real hero photo (rendered as a circular photo, not a rectangle - \
you just choose which bank photo, the renderer handles the shape).
3. BODY CONTENT - THIS ZONE IS DELIBERATELY FLEXIBLE. It is an ordered list (`blocks`) of typed content \
modules, not a fixed set of fields - the real reference email itself mixes several different module types \
rather than repeating one shape (a plain paragraph, a standalone product photo, a full-width section-header \
bar, a 3-item icon grid, a photo-plus-text module, a dark callout card, a checklist card). Choose which \
modules this specific email actually needs and in what order - a short logistics nudge might need only one \
`text` block; a longer onboarding or educational email might use four or five different modules, the way \
the reference does. See BLOCK TYPES below for what each one is for. Never pad this with modules the moment \
doesn't need just to look fuller, and never force a single module type to carry content it doesn't fit.
4. FOOTER & CLOSING CTA - the heading/bullets/button are REQUIRED on every email; the photo is OPTIONAL \
(a genuine design choice - a real second photo, bleeding off the edge of a dark panel, only when the email \
is long/text-heavy enough to need one or it's specifically asked for - not a fixed "every email gets two \
photos" rule). This is a real, separate closing moment from the top hero banner - restate the email's core \
promise/next step here, don't just repeat the opening headline verbatim.

BLOCK TYPES (zone 3, `blocks` - pick and order freely, per email):
- `text`: one or more plain paragraphs, centred, no card. The simplest block - use it for plain logistics/ \
explanation that doesn't need a visual treatment.
- `image_spotlight`: one standalone bank photo (e.g. a product shot), no text overlay - use it to show \
something (the product, the device) without narrating over it.
- `divider_header`: a short section-header line on its own full-width dark bar (e.g. "What Happens Next") - \
use it to open a new section within a longer email, the way the reference uses "What Mounjaro does".
- `icon_grid`: exactly 3 short one-line captions, each in its own small card with an icon - use it for 3 \
parallel, equally-weighted real benefits/facts (e.g. three things a treatment does), matching the \
reference's "Together, they help:" grid.
- `two_column`: a bank photo on one side, a heading + paragraph (+ optionally 2-4 short stacked lines) on \
the other - use it for an explainer that benefits from a supporting photo alongside it, matching the \
reference's "Why you're starting at 2.5mg" module.
- `callout_card`: a dark card with a short bold line + one supporting line - use it for a single, important \
reassurance or expectation-setting statement that deserves visual weight (matching the reference's "Weight \
loss may begin gradually, that's expected.").
- `checklist_card`: an optional heading/subtext above a card of 2-5 checkmarked lines - use it for a real \
sequence of steps or things to expect, matching the reference's "What happens next" list.
- `price_card`: a light card with a label, a price, and one button - ONLY when the flow brief gives you a \
real price and the price/package decision genuinely IS the point of this specific email (see PRICE CARD \
below). The reference email itself has no price card - most emails won't need one either.

When in doubt about zone 3, match the reference's own spirit (a small number of well-chosen, distinct \
modules that each earn their place) rather than either a single bare paragraph or an overstuffed list of \
every block type available.

BE SPECIFIC, NEVER GENERIC: every headline, subcopy, and body line must be written for THIS flow's exact \
real moment (see the flow brief below) - never a generic line that could be pasted into any telehealth \
brand's email unchanged. Reference the real, specific situation (what stage she's at, what decision is in \
front of her, what actually happens next) rather than a vague, safe-sounding restatement of the category. \
If a line would read the same on a completely different flow in this same category, rewrite it to be \
concrete to this one instead. This is the single most common way a draft reads as weak or substandard - \
treat genuine specificity as a hard requirement, not a nice-to-have.

VOICE - the four OVA tone-of-voice pillars, all four at once:
1. Smart not stuffy: you are talking to an informed adult. Intelligent, never academic.
2. Calm authority: straight-talking, rooted in care. You are the expert; you do not need to sell.
3. Trustworthy not transactional: evidence-led, never salesy, never a hard push.
4. Minimal and modern: plain language, sleek and unfussy. Say less.
DO: write with clarity and sharpness, explain any science in plain English, respect the reader, add \
warmth where it genuinely matters. DON'T: overpromise or exaggerate, use jargon, talk down or preach, \
sound cold or clinical, use any emotional manipulation, console or coach the reader about her own \
behaviour, or explain away why she did something.

HERO IMAGE SELECTION (REQUIRED - both the top hero and the closing photo, no text-only mode): go through the \
approved photo bank below and look at what each one actually shows and the real moment it fits; pick the \
one that genuinely argues the same thing the copy argues. Choose ONLY from the approved bank - never invent \
or request a new image, and never pick a photo just because it's the only one listed - a genuinely \
generic-but-warm lifestyle photo (see below) is a better choice than a forced, ill-fitting one. The bank \
always has at least a `general` option for every category, so hero: "none" should not be used for the TOP \
hero - it exists only as a last-resort escape hatch, not a normal choice. NEVER pick a photo whose own \
description flags it as an imperfect fit for its category or off the brand palette - if a photo needs an \
apology in its own description, it is not a genuine choice, skip it.

HOW MANY PHOTOS THIS EMAIL ACTUALLY NEEDS (real team guidance, not a fixed count): the top hero is the only \
photo every email needs. Every OTHER photo - the closing footer's `closing_image`, and any `image_spotlight`/ \
`two_column` block - is a genuine design choice, not a requirement. Add one only when it earns its place: \
the email is long or text-heavy enough that a second image usefully breaks it up, or a reviewer specifically \
asks for one. A short, simple email (a logistics nudge, a reminder) should typically use ONLY the top hero \
and leave `closing_image` null and skip image blocks entirely - do not add a second or third photo just \
because a reference example happened to have one. When you do use more than one photo in the same email, \
every one of them must be visually DISTINCT from every other one used in that SAME email - never pick the \
same key twice, and avoid two near-identical photos (e.g. two different generic portraits) when the bank \
has a more varied option available.

APPROVED PHOTO BANK:
{hero_catalog}

- Every key above is a real photo or product render, described exactly as it is - trust the description, \
never guess from the key name.
- This bank is deliberately small and honest: some categories only have general lifestyle photos (a real, \
current gap in the supplied photo set, not an oversight) - a `general` photo is a completely normal, \
expected choice for those, not a fallback to apologise for.
- Always set hero_rationale to one short line: why this hero fits this specific moment.

REAL BENEFITS ONLY, WHEREVER A BLOCK ASKS FOR ONE: an `icon_grid` item, a `two_column` block's `icon_items`, \
and a `checklist_card`'s items may ONLY restate this category's own real, approved service benefits (see \
REAL, APPROVED SERVICE BENEFITS in the category context below) or a plain factual/logistics step - lightly \
reworded for this moment is fine, inventing a new one is not. Never put a clinical claim/statistic in any \
of these short lines - those belong in a `text` or `two_column` paragraph only, and only per the claim rule \
below.

PRICE CARD: only use a `price_card` block when the flow brief gives you a real price to work with (see the \
price rule below) and the price/package decision genuinely IS the point of this specific email - a flow \
whose price is only a minor supporting detail (e.g. a consult reminder that happens to mention a fee) \
states that figure in a sentence instead and has no `price_card` block. `value` MUST be a short figure \
ONLY (e.g. "S$137", "S$99/month") - never a full sentence like "Save S$137 versus paying month to month". \
It renders in large bold display text, so a sentence there looks broken, not impressive. Put any extra \
context in `caption` instead (e.g. caption "versus paying month to month" alongside value "S$137") - never \
invented, never a percentage.

FOOTER & CLOSING CTA (the heading/bullets/button are REQUIRED, the photo is not): `closing_heading` is a \
short, punchy restatement of the email's core promise (not a copy-paste of the top headline) - the \
reference's own example, "A program designed for real life.", shows the right register: confident, plain, \
forward-looking. `closing_bullets` are 2-4 short real points (grounded in the category's real approved \
benefits or this flow's real specifics, never invented). `closing_cta_text` follows the same Title Case \
verb+My+noun convention as the main CTA (it can repeat the same action or be a natural close-out of it). \
`closing_image` is OPTIONAL - see HOW MANY PHOTOS above; when you do set it, it must be a distinct bank \
photo from every other image used in this email.

LANGUAGE RULES (absolute):
- British English spelling (personalised, programme, colour).
- NEVER an em-dash or long dash. Full stops or commas.
- No exclamation marks. The headline and any button/card labels are Title Case; body copy is sentence case.
- No app-speak ("activate", "tap", "unlock").
- CTA convention: verb + "My" + noun, e.g. "Book My Consultation", "Confirm My Refill", "Choose My Package".
- Never write a signature, a "Hi [name]," greeting, or a footer yourself - there is no named sender in \
this template (it is not a personal letter) and the footer is added automatically.

SINGAPORE ONLY - HARD RULE: OVA runs as two different businesses by market; you write for Singapore only, \
and Singapore's contraception, emergency contraception and intimate health lines do not exist in \
Malaysia. Never write anything that could read as Malaysia-facing contraception content, and never use a \
Malaysian Ringgit or any non-Singapore currency/price. If a request seems to concern a Malaysian patient, \
stop and set the note field rather than drafting.

COMPLIANCE (hard rules, from OVA_SG_CRM_AGENT_SCOPE.md SS4 - non-negotiable):
- NEVER name a prescription medicine or a specific branded product anywhere in the WORDS you write (copy, \
block text, price label). Describe the category and the benefit. (Product names in the internal category \
context below are for your grounding only, never customer-facing.) This rule is about naming/branding in \
TEXT - it is not a reason to avoid a real bank photo just because it happens to show a generic, unbranded \
product (a plain pill pack, an unlabelled vial): the photo bank is already curated so nothing in it shows a \
visible medicine name or brand, and using one of those approved photos is not a compliance issue.
- NEVER quote a discount percentage. A real dollar saving is fine when the flow brief gives you one \
verbatim; state the dollar figure, never a percentage. This is a genuinely different case from "inventing a \
number" - a real, sourced figure straight from the flow brief is exactly what this rule asks for, never \
paraphrase it into a vaguer non-number instead.
- NO medical advice, suitability assessment, dosing guidance, or symptom-matching in the copy. A \
patient's specific care decision is made in consultation, not in an email.
- ANY mention of a side effect (or of a symptom the patient might be having) must point them to a \
doctor, never be answered, reassured, or given a tip.
- Invent nothing. No fabricated statistics, testimonials, ratings, or numbers. The only figures you may \
state are this category's own real approved claims (see the category context), copied verbatim, and \
only when the flow brief says this moment fits one.
- No cure or guarantee language, no shame, no fear, no fake urgency.
- footer_disclaimer: one short, plain, accurate line appropriate to this category (e.g. that a specific \
concern or side effect should always go to a doctor) - never a medical claim, never invented detail.
- If the request is ambiguous, or nothing in the real data supports a confident draft, say so in the \
note field rather than guessing.

LEARNED RULES (standing requirements distilled from real past human feedback; apply every one):
{learned_rules}

{category_notes}

FLOW FOR THIS EMAIL: {flow_name}
{flow_brief}

Address the patient by first name: {first_name}.
"""


def _price_rule_text(flow: dict) -> str:
    if flow.get("price_context"):
        return (
            f"You MAY state this exact real figure once, plainly, if it genuinely helps: "
            f"{flow['price_context']}. Never a percentage, never a different number, never a product name."
        )
    return "HARD CONSTRAINT: never mention a price, a dollar amount, or a discount of any kind in this email."


def _stat_rule_text(flow: dict, category: str) -> str:
    if not flow.get("allow_stat"):
        return "HARD CONSTRAINT: do not restate any approved clinical claim or figure here - this moment is about logistics, not persuasion."
    cat = CATEGORIES.get(category, {})
    return (
        "This moment genuinely fits one of this category's real approved claims (see the category "
        "compliance notes) - use it verbatim if it strengthens the message, never paraphrased or upgraded. "
        f"Category: {cat.get('label', category)}."
    )


def _build_hero_catalog(category: str = DEFAULT_CATEGORY) -> str:
    """Real photo catalog FOR THIS CATEGORY ONLY - same reasoning as
    andSons' copywriter_agent.py: showing the full bank regardless of
    category would put an actively wrong image in front of the model."""
    bank = hero_bank_for_category(category)
    lines = [f'- "{key}": {entry["description"]}. Best for: {entry["moment"]}.' for key, entry in bank.items()]
    if not lines:
        return "(No reviewed photos exist for this category yet - always choose \"none\" (text-first).)"
    return "\n".join(lines) + recent_heroes_prompt_block(category)


def _build_flow_brief(flow_slug: str, category: Optional[str] = None) -> str:
    flow = FLOW_BY_SLUG.get(flow_slug)
    if flow is None:
        flow = next(iter(FLOW_BY_SLUG.values()))
    effective_category = category or flow.get("category", DEFAULT_CATEGORY)

    lines = [
        f"Flow: {flow['label']} (track: {flow['track']}).",
        f"Reader: {flow['audience']}",
        f"Goal: {flow['goal']}",
        f"The action this email points to: {flow['cta']}. Render it as a real Title Case button label, "
        "verb + My + noun convention (cta_text).",
        _price_rule_text(flow),
        _stat_rule_text(flow, effective_category),
    ]
    if flow["track"] == "rx":
        lines.append(
            "This is a doctor-led decision moment - never imply the patient can change or stop a "
            "prescribed treatment herself, and never assess her suitability or symptoms in the email."
        )
    return "\n".join(lines)


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    paragraphs: List[str] = Field(min_length=1, max_length=3, description="1-3 short plain paragraphs, sentence case, centred, no card.")


class ImageSpotlightBlock(BaseModel):
    type: Literal["image_spotlight"] = "image_spotlight"
    image: Literal[tuple(HERO_KEYS)] = Field(description="A real bank key - a standalone photo shown with no text overlay.")  # type: ignore[valid-type]


class DividerHeaderBlock(BaseModel):
    type: Literal["divider_header"] = "divider_header"
    text: str = Field(description="A short section-header line (2-5 words, Title Case) shown on its own full-width dark bar, e.g. 'What Happens Next'.")


class IconGridItem(BaseModel):
    heading: str = Field(description="One short caption line for this grid item - a real approved benefit/fact, never invented.")


class IconGridBlock(BaseModel):
    type: Literal["icon_grid"] = "icon_grid"
    intro: Optional[str] = Field(default=None, description="Optional one-line lead-in above the grid, e.g. 'Together, they help:'. Null if not needed.")
    items: List[IconGridItem] = Field(min_length=3, max_length=3, description="Exactly 3 items, each a real approved benefit/fact.")


class TwoColumnBlock(BaseModel):
    type: Literal["two_column"] = "two_column"
    image: Literal[tuple(HERO_KEYS)] = Field(description="A real bank key, distinct from every other image used in this email.")  # type: ignore[valid-type]
    image_side: Literal["left", "right"] = Field(default="left", description="Which side the photo sits on.")
    heading: str = Field(description="Short heading for this module, Title Case.")
    paragraph: str = Field(description="One short explainer paragraph, sentence case.")
    icon_items: Optional[List[str]] = Field(
        default=None, max_length=4,
        description="OPTIONAL: 2-4 short stacked lines under the paragraph, each a real approved benefit/fact. Null if not needed.",
    )


class CalloutCardBlock(BaseModel):
    type: Literal["callout_card"] = "callout_card"
    heading: str = Field(description="One short, bold reassurance/expectation-setting line, sentence case.")
    body: str = Field(description="One short supporting line under the heading, sentence case.")


class ChecklistCardBlock(BaseModel):
    type: Literal["checklist_card"] = "checklist_card"
    heading: Optional[str] = Field(default=None, description="OPTIONAL centred heading above the card, Title Case.")
    subtext: Optional[str] = Field(default=None, description="OPTIONAL one-line lead-in under the heading, sentence case.")
    items: List[str] = Field(min_length=2, max_length=5, description="2-5 short checkmarked lines - real steps/facts, never invented.")
    closing_note: Optional[str] = Field(default=None, description="OPTIONAL one short reassurance line shown under the card, sentence case.")


class PriceCardBlock(BaseModel):
    type: Literal["price_card"] = "price_card"
    label: str = Field(description="Short label above the price, e.g. 'Three-Month Package'.")
    value: str = Field(description="ONLY the short price/saving FIGURE itself from the flow brief, verbatim - e.g. 'S$137' or 'S$99/month'. NEVER a full sentence, never invented, never a percentage. This renders in large bold display text, so it must read as a number at a glance - if you want to add context (e.g. 'versus paying month to month'), put that in `caption`, not here.")
    caption: Optional[str] = Field(default=None, description="OPTIONAL one short line of plain context shown under the price in smaller text, e.g. 'versus paying month to month', sentence case. Null if the label+value already say enough.")
    cta_text: str = Field(description="Button label inside the card, Title Case verb+My+noun.")


Block = Annotated[
    Union[TextBlock, ImageSpotlightBlock, DividerHeaderBlock, IconGridBlock, TwoColumnBlock, CalloutCardBlock, ChecklistCardBlock, PriceCardBlock],
    Field(discriminator="type"),
]


class EmailContent(BaseModel):
    subject: str = Field(description="The email's headline, shown big and centred at the top - Title Case, 1-2 short lines, specific and calm, never a clinical label.")
    preheader: str = Field(description="One sentence of inbox preview text - plain, sentence case.")
    intro: str = Field(description="ONE short sentence of subcopy under the headline, centred, sentence case - plainly states what this email is about.")
    hero: Literal[tuple(HERO_KEYS)] = Field(  # type: ignore[valid-type]
        description="The TOP hero image (zone 2): one of the approved bank keys. Every category has a "
        "'general' option, so this is effectively always a real key - 'none' is a last-resort escape "
        "hatch only, not a normal choice for this template."
    )
    hero_rationale: Optional[str] = Field(default=None, description="One short line: why this hero fits this specific moment.")
    cta_text: str = Field(description="The main CTA button label, Title Case, verb + My + noun convention, e.g. 'Book My Consultation'.")
    blocks: List[Block] = Field(
        min_length=1, max_length=8,
        description="REQUIRED: the ordered list of body-content modules (zone 3) this specific email "
        "needs - see BLOCK TYPES above. A short logistics email may need only 1; a longer onboarding/"
        "educational email may use 6-8, matching the reference's own real mix.",
    )
    closing_heading: str = Field(description="REQUIRED: the closing footer's own heading (zone 4) - a short, punchy restatement of the email's core promise, not a copy of the top headline.")
    closing_bullets: List[str] = Field(min_length=2, max_length=4, description="REQUIRED: 2-4 short real checkmark bullets for the closing footer.")
    closing_cta_text: str = Field(description="REQUIRED: the closing footer's own CTA button label, Title Case verb+My+noun.")
    closing_image: Optional[Literal[tuple(HERO_KEYS)]] = Field(  # type: ignore[valid-type]
        default=None,
        description="OPTIONAL: a second real bank photo for the closing footer (zone 4), distinct from the "
        "top hero and from every image used in `blocks`. This is a genuine design choice, not a fixed "
        "requirement - use it when this specific email is long/text-heavy enough to benefit from a second "
        "image breaking it up, or when it was specifically asked for. A short, simple email should leave "
        "this null and render as plain text in the closing footer - do not add a second photo just because "
        "a reference example happened to have one.",
    )
    footer_disclaimer: Optional[str] = Field(
        default=None,
        description="A short, plain, accurate one-line disclaimer appropriate to this category (e.g. "
        "pointing a specific concern to a doctor) - see the compliance rules. Never a medical claim.",
    )
    note: Optional[str] = Field(
        default=None,
        description="Leave null unless the CURRENT, newest feedback asks for something a hard rule "
        "(compliance, the language rules, Singapore-only, invent-nothing) does not allow: apply the "
        "closest compliant interpretation and use this field to say in one plain sentence what was asked "
        "and why it could not be done literally. Never fire this for a change that simply has no dedicated "
        "field - a wording or content change is a plain edit.",
    )


def _sanitize_block(block: "Block") -> "Block":
    if isinstance(block, TextBlock):
        return block.model_copy(update={"paragraphs": [sanitize_text(p) for p in block.paragraphs]})
    if isinstance(block, ImageSpotlightBlock):
        return block
    if isinstance(block, DividerHeaderBlock):
        return block.model_copy(update={"text": sanitize_text(block.text)})
    if isinstance(block, IconGridBlock):
        return block.model_copy(update={
            "intro": sanitize_text(block.intro) if block.intro else None,
            "items": [item.model_copy(update={"heading": sanitize_text(item.heading)}) for item in block.items],
        })
    if isinstance(block, TwoColumnBlock):
        return block.model_copy(update={
            "heading": sanitize_text(block.heading),
            "paragraph": sanitize_text(block.paragraph),
            "icon_items": [sanitize_text(t) for t in block.icon_items] if block.icon_items else None,
        })
    if isinstance(block, CalloutCardBlock):
        return block.model_copy(update={"heading": sanitize_text(block.heading), "body": sanitize_text(block.body)})
    if isinstance(block, ChecklistCardBlock):
        return block.model_copy(update={
            "heading": sanitize_text(block.heading) if block.heading else None,
            "subtext": sanitize_text(block.subtext) if block.subtext else None,
            "items": [sanitize_text(i) for i in block.items],
            "closing_note": sanitize_text(block.closing_note) if block.closing_note else None,
        })
    if isinstance(block, PriceCardBlock):
        return block.model_copy(update={
            "label": sanitize_text(block.label), "value": sanitize_text(block.value),
            "caption": sanitize_text(block.caption) if block.caption else None,
            "cta_text": sanitize_text(block.cta_text),
        })
    return block


def _sanitize_content(content: EmailContent) -> EmailContent:
    return content.model_copy(
        update={
            "subject": sanitize_text(content.subject),
            "preheader": sanitize_text(content.preheader),
            "intro": sanitize_text(content.intro),
            "hero_rationale": sanitize_text(content.hero_rationale) if content.hero_rationale else None,
            "cta_text": sanitize_text(content.cta_text),
            "blocks": [_sanitize_block(b) for b in content.blocks],
            "closing_heading": sanitize_text(content.closing_heading),
            "closing_bullets": [sanitize_text(b) for b in content.closing_bullets],
            "closing_cta_text": sanitize_text(content.closing_cta_text),
            "footer_disclaimer": sanitize_text(content.footer_disclaimer) if content.footer_disclaimer else None,
            "note": sanitize_text(content.note) if content.note else None,
        }
    )


def resolve_hero(content: EmailContent) -> dict:
    """Turns the Copywriter's (or, after Creative Director review, the
    final) hero decision into an actual displayable image - real andSons
    reasoning: bank keys resolve to their real approved local URL; 'none'
    stays text-first. Selection is bank-only, no generation fallback."""
    hero = content.hero
    if hero in HERO_BANK:
        return {
            "hero": hero, "hero_image_url": HERO_BANK[hero]["url"],
            "hero_source": "bank", "hero_rationale": content.hero_rationale,
        }
    return {"hero": "none", "hero_image_url": None, "hero_source": "none", "hero_rationale": content.hero_rationale}


def _apply_hero_lock(content: "EmailContent", locked_hero: Optional[dict]) -> "EmailContent":
    """Deterministic enforcement backing up whatever this round already
    decided - real, reported problem this fixes (see andSons'
    copywriter_agent.py): a hero changing on its own initiative on a
    revision round that never asked for a different image. No-op when
    locked_hero is None or 'none'."""
    if not locked_hero or not locked_hero.get("hero") or locked_hero["hero"] == "none":
        return content
    if content.hero != locked_hero["hero"]:
        logger.warning("Hero changed to %r on a revision that didn't ask for an image change (was %r) - forcing it back.", content.hero, locked_hero["hero"])
    return content.model_copy(update={"hero": locked_hero["hero"]})


def _render_block_text(block: "Block", first_name: str) -> list:
    """One block's plain-text render, in the shape the Sweeper reads.
    Mirrors what email_image_renderer.py's block renderers draw."""
    if isinstance(block, TextBlock):
        return [p.replace("[name]", first_name) for p in block.paragraphs] + [""]
    if isinstance(block, ImageSpotlightBlock):
        bank_entry = HERO_BANK.get(block.image, {})
        return [f'[IMAGE: {bank_entry.get("description", block.image)}]', ""]
    if isinstance(block, DividerHeaderBlock):
        return [f"=== {block.text} ===", ""]
    if isinstance(block, IconGridBlock):
        lines = [block.intro] if block.intro else []
        lines += [f"[icon] {item.heading}" for item in block.items]
        return lines + [""]
    if isinstance(block, TwoColumnBlock):
        bank_entry = HERO_BANK.get(block.image, {})
        lines = [f'[IMAGE ({block.image_side}): {bank_entry.get("description", block.image)}]', block.heading, block.paragraph.replace("[name]", first_name)]
        lines += [f"[icon] {t}" for t in (block.icon_items or [])]
        return lines + [""]
    if isinstance(block, CalloutCardBlock):
        return [f"[CALLOUT CARD: {block.heading} - {block.body}]", ""]
    if isinstance(block, ChecklistCardBlock):
        lines = [x for x in (block.heading, block.subtext) if x]
        lines += [f"[check] {i}" for i in block.items]
        if block.closing_note:
            lines.append(block.closing_note)
        return lines + [""]
    if isinstance(block, PriceCardBlock):
        caption = f" | {block.caption}" if block.caption else ""
        return [f"[PRICE CARD: {block.label} | {block.value}{caption} | {block.cta_text}]", ""]
    return []


def render_email(content: EmailContent, first_name: str, hero_info: Optional[dict] = None) -> str:
    """Plain-text render of the 4-zone template, in the shape the Sweeper
    reads. Mirrors what email_image_renderer.py draws."""
    parts = [
        f"Subject: {content.subject}",
        f"Preheader: {content.preheader}",
        "",
        "[LOGO]",
        content.subject.replace("[name]", first_name),
        content.intro.replace("[name]", first_name),
        f"[{content.cta_text}]",
        "",
    ]
    if hero_info and hero_info.get("hero") != "none" and hero_info.get("hero_image_url"):
        bank_entry = HERO_BANK.get(hero_info["hero"], {})
        parts.append(f'[HERO IMAGE: {bank_entry.get("description", "hero image")}]')
        parts.append("")

    for block in content.blocks:
        parts += _render_block_text(block, first_name)

    parts.append("[FOOTER & CLOSING CTA]")
    parts.append(content.closing_heading)
    parts += [f"[check] {b}" for b in content.closing_bullets]
    parts.append(f"[{content.closing_cta_text}]")
    if content.closing_image and content.closing_image != "none":
        closing_entry = HERO_BANK.get(content.closing_image, {})
        parts.append(f'[CLOSING IMAGE: {closing_entry.get("description", content.closing_image)}]')
    parts.append("")

    if content.footer_disclaimer:
        parts.append(content.footer_disclaimer)
    parts.append("Unsubscribe")
    return "\n".join(p for p in parts if p is not None).strip()


_INSIGHT_BRIEF_INSTRUCTION = """INTERNAL STRATEGY CONTEXT (why this email is being written now) - real \
business/marketing data to inform your ANGLE only. NOT customer-facing:
- Never quote a raw number, percentage, campaign name, or a phrase like "engagement dropped" - the \
patient must never sense this email exists because of an internal metric.
- Use it only to choose which real, approved point to lead with.
- Every compliance rule still applies in full.

{brief}
"""

_TEMPLATE_REFERENCE_INSTRUCTION = """REFERENCE IMAGE (a reviewer uploaded one) - read this as feedback on \
CONTENT within the fixed template above, never as permission to change the template's structure. The \
template itself (headline/subcopy/hero/CTA, callouts, secondary section, features, optional price card, \
footer) is fixed and described in full above - it does not change based on what this image shows. Use \
this reference only to judge whether your callouts/features/headline/tone genuinely match what was asked \
for, or to identify a specific wording/content correction. Never treat it as license to add, remove, or \
reorder a structural block, add a second image, or introduce any element the template above doesn't \
already have.

{template_description}
"""


def _template_reference_block(template_reference: Optional[str]) -> str:
    if not template_reference:
        return ""
    escaped = template_reference.replace("{", "{{").replace("}", "}}")
    return "\n\n" + _TEMPLATE_REFERENCE_INSTRUCTION.format(template_description=escaped)


def generate_email(
    flow_name: str, first_name: str, correction: Optional[str] = None, insight_brief: Optional[str] = None,
    category: str = DEFAULT_CATEGORY, locked_hero: Optional[dict] = None, template_reference: Optional[str] = None, **_ignored,
) -> dict:
    llm = get_llm("COPYWRITER", temperature=_CREATIVE_TEMPERATURE)
    structured_llm = llm.with_structured_output(EmailContent)

    flow_brief = _build_flow_brief(flow_name, category)
    system_text = SYSTEM_PROMPT.format(
        flow_name=flow_name, flow_brief=flow_brief, first_name=first_name,
        hero_catalog=_build_hero_catalog(category),
        learned_rules=learned_rules_text(), category_notes=category_notes_text(category),
    )
    if insight_brief:
        system_text += "\n\n" + _INSIGHT_BRIEF_INSTRUCTION.format(brief=insight_brief)
    system_text += _template_reference_block(template_reference)

    human_text = f"Write the {flow_name} email for {first_name}."
    if correction:
        human_text += (
            "\n\nThis is a REVISION. The previous draft failed review. Keep every rule above in force "
            "and additionally apply this correction:\n" + correction
        )

    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm

    content, last_exc = invoke_with_retry(chain, label="Copywriter structured-output call")
    if content is None:
        raise RuntimeError(f"Copywriter failed to produce a draft after retrying ({last_exc}).") from last_exc

    content = _sanitize_content(content)

    # Email Creative Director: a genuine second, independent pass over the
    # hero choice only - see creative_director_agent.py's module docstring.
    direction = direct_touchpoint(content.model_dump(), [], category=category)
    content = content.model_copy(update={"hero": direction["hero"]})
    content = _apply_hero_lock(content, locked_hero)

    hero_info = resolve_hero(content)
    rendered = render_email(content, first_name, hero_info=hero_info)
    record_hero_use(category, hero_info["hero"])
    result_content = content.model_dump()
    result_content.update({"hero": hero_info["hero"], "hero_image_url": hero_info["hero_image_url"], "hero_source": hero_info["hero_source"]})
    return {"content": result_content, "rendered_text": rendered, "hero_notes": []}


# --- Multi-touchpoint flow generation -------------------------------------
# OVA flows are a short real lifecycle sequence of Email + WhatsApp
# touchpoints (see flows.py) - the same two channels andSons uses.

WHATSAPP_SYSTEM_PROMPT = """You are writing ONE WhatsApp message for OVA Singapore (women's health \
telehealth) - a short, timely, personal nudge from the patient's care team, not a shrunk-down email. \
Plain conversational text, 2-4 short lines, warm and direct. It uses the WABA "Call-To-Action" template \
shape: a link-preview title above the message, then the message body (its first line a short bold \
greeting/hook), then exactly one button. The link-preview title and the one button are the template's \
own structured fields, not something you write inline.

VOICE - the four OVA tone-of-voice pillars: smart not stuffy, calm authority, trustworthy not \
transactional, minimal and modern. Plain, warm, never salesy.

LANGUAGE RULES (absolute): British English spelling. NEVER an em-dash or long dash. No exclamation \
marks. Sentence case. No app-speak.

COMPLIANCE (hard rules): NEVER name a prescription medicine or specific product. A care decision is made \
in consultation, never in a message: no medical advice, suitability assessment, dosing, or symptom- \
matching. Any side-effect mention points to a doctor, never answered. Invent nothing.
{price_rule}
{stat_rule}

No defensive meta-commentary. Never lead with money.

LEARNED RULES (apply every one):
{learned_rules}

{category_notes}

FLOW: {flow_name}
{flow_brief}

THIS TOUCHPOINT'S MOMENT IN THE FLOW (timing: {timing}): {intent}

{prior_context}

Address the patient as NAME (a literal placeholder token, never a real name). Write ONLY the message \
body text (2-4 short lines).
"""


class WhatsAppContent(BaseModel):
    link_title: str = Field(description="Short headline for the WhatsApp link-preview card, e.g. 'Your care with OVA' - plain, describes the linked page, never an unverifiable claim.")
    hook_line: str = Field(description="ONE short warm greeting/hook sentence addressed to NAME - the first line, shown bold, e.g. 'Hi NAME, a quick note about your refill.'")
    body: str = Field(description="1-3 more short plain-text lines after the hook line. 2-4 short lines total with the hook. No signature, no link pasted in.")
    cta_text: str = Field(description="The single action, as a short natural phrase, e.g. 'Confirm my refill' - shown as the one button this message carries.")
    note: Optional[str] = Field(
        default=None,
        description="Leave null unless the CURRENT feedback asks for something a hard rule does not allow - apply the closest compliant version and say why in one plain sentence. Not for plain wording edits.",
    )


def _sanitize_whatsapp(content: WhatsAppContent) -> WhatsAppContent:
    return content.model_copy(update={
        "link_title": sanitize_text(content.link_title), "hook_line": sanitize_text(content.hook_line),
        "body": sanitize_text(content.body), "cta_text": sanitize_text(content.cta_text),
        "note": sanitize_text(content.note) if content.note else None,
    })


def render_whatsapp(content: WhatsAppContent) -> str:
    return f"[LINK PREVIEW: {content.link_title}]\n\n{content.hook_line}\n{content.body}\n\n[{content.cta_text}]"


def _prior_touchpoints_context(prior: list) -> str:
    if not prior:
        return "This is the FIRST touchpoint in the flow - nothing has been sent yet."
    lines = [
        "EARLIER TOUCHPOINTS ALREADY SENT in this same flow - this one must read as the next step in one "
        "continuous conversation, never a repeat:"
    ]
    for p in prior:
        lines.append(f"- Touchpoint {p['n']} ({p['channel']}, {p['timing']}): {p['summary']}")
    return "\n".join(lines)


def generate_touchpoint(
    flow_name: str, step: dict, prior_summaries: list, correction: Optional[str] = None,
    insight_brief: Optional[str] = None, category: str = DEFAULT_CATEGORY, locked_hero: Optional[dict] = None, template_reference: Optional[str] = None, **_ignored,
) -> dict:
    if step["channel"] == "email":
        return generate_flow_email_touchpoint(flow_name, step, prior_summaries, correction=correction, insight_brief=insight_brief, category=category, locked_hero=locked_hero, template_reference=template_reference)
    if step["channel"] == "whatsapp":
        return generate_flow_whatsapp_touchpoint(flow_name, step, prior_summaries, correction=correction, insight_brief=insight_brief, category=category, template_reference=template_reference)
    raise ValueError(f"Unknown or unsupported channel for OVA: {step['channel']!r} (only email/whatsapp).")


def generate_flow(
    flow_name: str, insight_brief: Optional[str] = None, cadence: Optional[list] = None,
    category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None,
) -> dict:
    flow = FLOW_BY_SLUG.get(flow_name)
    if flow is None:
        raise ValueError(f"Unknown flow: {flow_name}")

    touchpoints = []
    prior_summaries = []
    for step in (cadence or flow["cadence"]):
        try:
            touchpoint = generate_touchpoint(flow_name, step, prior_summaries, insight_brief=insight_brief, category=category, template_reference=template_reference)
        except RuntimeError as exc:
            logger.error("Touchpoint %d (%s) failed to generate after retrying: %s", step["n"], step["channel"], exc)
            touchpoint = {
                "n": step["n"], "channel": step["channel"], "timing": step["timing"], "intent": step["intent"],
                "content": None, "rendered_text": None, "generation_failed": True,
            }
        touchpoints.append(touchpoint)
        prior_summaries.append(_touchpoint_summary(touchpoint))
    return {"flow_name": flow_name, "touchpoints": touchpoints}


def _touchpoint_summary(touchpoint: dict) -> dict:
    if touchpoint.get("generation_failed"):
        return {
            "n": touchpoint["n"], "channel": touchpoint["channel"], "timing": touchpoint["timing"],
            "summary": "(this step failed to generate and needs a manual retry - nothing was actually sent)",
        }
    content = touchpoint["content"]
    if touchpoint["channel"] == "email":
        summary = f"Subject '{content['subject']}' - {content.get('intro', '')}"
    else:  # whatsapp
        summary = f"{content['hook_line']} {content['body']}"
    return {
        "n": touchpoint["n"], "channel": touchpoint["channel"], "timing": touchpoint["timing"],
        "hero": content.get("hero") if touchpoint["channel"] == "email" else None,
        "summary": summary,
    }


def generate_flow_email_touchpoint(
    flow_name: str, step: dict, prior_summaries: list, correction: Optional[str] = None,
    insight_brief: Optional[str] = None, category: str = DEFAULT_CATEGORY, locked_hero: Optional[dict] = None, template_reference: Optional[str] = None, **_ignored,
) -> dict:
    flow_brief = _build_flow_brief(flow_name, category)
    llm = get_llm("COPYWRITER", temperature=_CREATIVE_TEMPERATURE)
    structured_llm = llm.with_structured_output(EmailContent)
    system_text = SYSTEM_PROMPT.format(
        flow_name=flow_name, flow_brief=flow_brief, first_name="NAME",
        hero_catalog=_build_hero_catalog(category),
        learned_rules=learned_rules_text(), category_notes=category_notes_text(category),
    )
    system_text += (
        f"\n\nTHIS TOUCHPOINT'S MOMENT IN THE FLOW (touchpoint {step['n']}, timing: {step['timing']}): "
        f"{step['intent']}\n\n" + _prior_touchpoints_context(prior_summaries)
    )
    if insight_brief:
        system_text += "\n\n" + _INSIGHT_BRIEF_INSTRUCTION.format(brief=insight_brief)
    system_text += _template_reference_block(template_reference)
    human_text = f"Write touchpoint {step['n']} ({step['timing']}) of the {flow_name} flow for NAME."
    if correction:
        human_text += "\n\nThis is a REVISION. The previous draft of this touchpoint failed review. Keep every rule above in force and additionally apply this correction:\n" + correction

    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm

    content, last_exc = invoke_with_retry(chain, label="Copywriter flow touchpoint call")
    if content is None:
        raise RuntimeError(f"Copywriter failed to produce touchpoint {step['n']} ({last_exc}).") from last_exc

    content = _sanitize_content(content)

    direction = direct_touchpoint(content.model_dump(), prior_summaries, category=category)
    content = content.model_copy(update={"hero": direction["hero"]})
    content = _apply_hero_lock(content, locked_hero)

    hero_info = resolve_hero(content)
    rendered = render_email(content, "NAME", hero_info=hero_info)
    record_hero_use(category, hero_info["hero"])
    result_content = content.model_dump()
    result_content.update({"hero": hero_info["hero"], "hero_image_url": hero_info["hero_image_url"], "hero_source": hero_info["hero_source"], "art_rationale": direction.get("art_rationale")})
    return {
        "n": step["n"], "channel": "email", "timing": step["timing"], "intent": step["intent"],
        "content": result_content, "rendered_text": rendered,
    }


def generate_flow_whatsapp_touchpoint(
    flow_name: str, step: dict, prior_summaries: list, correction: Optional[str] = None,
    insight_brief: Optional[str] = None, category: str = DEFAULT_CATEGORY, template_reference: Optional[str] = None, **_ignored,
) -> dict:
    flow = FLOW_BY_SLUG[flow_name]
    flow_brief = _build_flow_brief(flow_name, category)
    llm = get_llm("COPYWRITER", temperature=_CREATIVE_TEMPERATURE)
    structured_llm = llm.with_structured_output(WhatsAppContent)
    system_text = WHATSAPP_SYSTEM_PROMPT.format(
        flow_name=flow_name, flow_brief=flow_brief, timing=step["timing"], intent=step["intent"],
        price_rule=_price_rule_text(flow), stat_rule=_stat_rule_text(flow, category),
        prior_context=_prior_touchpoints_context(prior_summaries), learned_rules=learned_rules_text(),
        category_notes=category_notes_text(category),
    )
    if insight_brief:
        system_text += "\n\n" + _INSIGHT_BRIEF_INSTRUCTION.format(brief=insight_brief)
    system_text += _template_reference_block(template_reference)
    human_text = f"Write touchpoint {step['n']} ({step['timing']}) of the {flow_name} flow, a WhatsApp message for NAME."
    if correction:
        human_text += "\n\nThis is a REVISION. The previous draft of this touchpoint failed review. Keep every rule above in force and additionally apply this correction:\n" + correction

    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm

    content, last_exc = invoke_with_retry(chain, label="Copywriter WhatsApp touchpoint call")
    if content is None:
        raise RuntimeError(f"Copywriter failed to produce touchpoint {step['n']} ({last_exc}).") from last_exc

    content = _sanitize_whatsapp(content)
    rendered = render_whatsapp(content)
    return {
        "n": step["n"], "channel": "whatsapp", "timing": step["timing"], "intent": step["intent"],
        "content": content.model_dump(), "rendered_text": rendered,
    }


class EmailIntent(BaseModel):
    mode: Literal["direct", "insight", "personalized"] = Field(
        description="'direct' if this names or clearly implies one specific flow. 'insight' if it "
        "describes a business problem/signal to work out the flow from, or explicitly asks for live data. "
        "'personalized' if the request explicitly asks for an email tailored to ONE SPECIFIC real patient "
        "using their OWN real data/history (e.g. 'look up order #4521's real history and personalize this "
        "for her') - the giveaway is a real identifier (an order/customer/subscription ID, not just a "
        "first name for the greeting) plus a request to ground the content in THAT PERSON's own real "
        "situation, not a general segment or business signal."
    )
    flow_name: Optional[Literal[tuple(VALID_FLOW_SLUGS)]] = Field(  # type: ignore[valid-type]
        default=None, description="For 'direct': the flow slug. For 'insight'/'personalized': only if the message names one. Null if unclear - do not guess.",
    )
    signal_question: Optional[str] = Field(default=None, description="For 'insight' only: the business signal/question as its own clean sentence. Null otherwise.")
    patient_identifier: Optional[str] = Field(
        default=None,
        description="For 'personalized' only: the real identifier given for the specific patient to look "
        "up (an order/customer/subscription ID, copied exactly as given). Never a name/email/phone alone "
        "- if the request only gives a first name with no real identifier, that is NOT enough to be "
        "'personalized' (treat it as 'direct' instead, since there's no real record to look up). Null "
        "otherwise.",
    )
    category: Literal[tuple(VALID_CATEGORY_SLUGS)] = Field(  # type: ignore[valid-type]
        default=DEFAULT_CATEGORY,
        description="Which OVA SG category: 'contraception', 'emergency_contraception', 'intimate_health', or 'weight_loss'. Default 'contraception' when there's no signal either way.",
    )


def parse_email_request(text: str) -> dict:
    llm = get_llm("COPYWRITER")
    structured_llm = llm.with_structured_output(EmailIntent)
    catalog = "\n".join(f'- "{slug}": {flow["label"]} ({flow["track"]} track, {flow["category"]}) - {flow["audience"]}' for slug, flow in FLOW_BY_SLUG.items())
    system_text = (
        "You classify a free-text CRM request against this real OVA Singapore flow catalog (slug: label - "
        "who it's for):\n" + catalog + "\n\nThere is no real customer in this conversation for 'direct'/"
        "'insight' mode - every draft addresses a generic 'NAME' placeholder there. 'personalized' mode is "
        "the one exception - it requires a real identifier (order/customer/subscription ID), not just a "
        "name, to look up. For 'direct' mode, leave flow_name null rather than guessing. For 'insight'/"
        "'personalized' mode, only fill flow_name if the message names/implies one. Also classify "
        "'category'."
    )
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", text)])
    chain = prompt | structured_llm
    try:
        result: EmailIntent = chain.invoke({})
    except Exception:
        logger.warning("parse_email_request: model failed to return structured output for %r", text)
        return {"mode": "unclear", "flow_name": None, "signal_question": None, "patient_identifier": None, "category": DEFAULT_CATEGORY}
    return {"mode": result.mode, "flow_name": result.flow_name, "signal_question": result.signal_question, "patient_identifier": result.patient_identifier, "category": result.category}


class FlowPick(BaseModel):
    flow_name: Optional[Literal[tuple(VALID_FLOW_SLUGS)]] = Field(  # type: ignore[valid-type]
        default=None, description="The single best-fitting flow slug for this signal, or null if none genuinely fit - do not force a pick.",
    )


def pick_flow_for_signal(question: str, brief_text: str) -> Optional[str]:
    llm = get_llm("COPYWRITER")
    structured_llm = llm.with_structured_output(FlowPick)
    catalog = "\n".join(f'- "{slug}": {flow["label"]} ({flow["track"]} track, {flow["category"]}) - {flow["audience"]} Goal: {flow["goal"]}' for slug, flow in FLOW_BY_SLUG.items())
    system_text = (
        "Given this investigated business signal and the real OVA Singapore flow catalog below, pick the "
        "ONE flow whose real trigger/audience/goal genuinely fits sending this email right now. Leave "
        "flow_name null if nothing genuinely fits.\n\n" + catalog
    )
    human_text = f"Signal: {question}\n\nInvestigation findings:\n{brief_text}"
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm
    try:
        result: FlowPick = chain.invoke({})
    except Exception:
        logger.warning("pick_flow_for_signal: model failed to return structured output for %r", question)
        return None
    return result.flow_name


class _FlowFitCheck(BaseModel):
    fits: bool = Field(description="True only if the request GENUINELY describes this flow's own real trigger/audience/goal - not just a topically similar theme.")


def flow_genuinely_fits(request_text: str, flow_slug: str) -> bool:
    flow = FLOW_BY_SLUG.get(flow_slug)
    if flow is None:
        return False
    llm = get_llm("COPYWRITER", temperature=0.0)
    structured_llm = llm.with_structured_output(_FlowFitCheck)
    system_text = (
        f"A CRM request was matched to the real OVA '{flow['label']}' flow. Its real definition - trigger: "
        f"{flow['trigger']} | audience: {flow['audience']} | goal: {flow['goal']}.\n\nDoes the request "
        "below genuinely describe sending an email for THIS SAME real trigger/audience/goal, or something "
        "meaningfully different that just shares a theme?"
    )
    escaped_request = request_text.replace("{", "{{").replace("}", "}}")
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", escaped_request)])
    chain = prompt | structured_llm
    try:
        result: _FlowFitCheck = chain.invoke({})
    except Exception as exc:  # noqa: BLE001 - fail safe
        logger.warning("flow_genuinely_fits: verification call failed for flow=%r (%s) - assuming it fits.", flow_slug, exc)
        return True
    return result.fits

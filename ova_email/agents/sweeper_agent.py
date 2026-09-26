"""Sweeper agent - the Pre-Launch brand-QA gate for OVA Singapore CRM
touchpoints (email + WhatsApp).

Email format is the team's real, stakeholder-confirmed 4-zone template (see
copywriter_agent.py's module docstring): Logo -> Hero banner & CTA -> Body
content (a flexible, ordered list of typed blocks) -> Footer & Closing CTA.
Zones 1/2/4's TEXT (heading/bullets/button) is fixed and required on every
email; the TOP hero photo (zone 2) is required too, but every other photo
- the closing footer's photo, and any block's own image - is a genuine
design choice (per the real stakeholder team's own confirmation: images
beyond the one required hero are added when an email is long/text-heavy
enough to need breaking up, or specifically requested - never a fixed
count). Zone 3's block list is likewise deliberately flexible in shape, so
the Sweeper does not hard-fail a particular block count or mix, or a
missing closing/block photo - it hard-fails a photo that IS present but
mismatched (including one whose own description flags it as an
imperfect/off-brand fit - see image_bank.py's own real fix history for why
that's a real, previously-caught failure mode), two images in the SAME
email that are identical (a real, Python-level check - see
`_hero_deterministic_issues`), a benefit-style line in a block that isn't
one of the category's own real approved benefits, a price_card block that
doesn't match the flow's real price exactly, anything that breaks the four
tone-of-voice pillars, and every compliance rule from
OVA_SG_CRM_AGENT_SCOPE.md SS4.
"""
import logging
from typing import List, Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from categories import DEFAULT_CATEGORY, category_notes_text
from flows import FLOW_BY_SLUG
from image_bank import HERO_BANK, hero_bank_for_category

from .copywriter_agent import _build_flow_brief, _price_rule_text, _stat_rule_text
from .learned_rules_agent import learned_rules_text
from .llm_provider import get_llm, invoke_with_retry

logger = logging.getLogger("sweeper_agent")

SYSTEM_PROMPT = """You are the Pre-Launch Sweeper for OVA Singapore (women's health telehealth) - the last \
automated check before an email is shown to a human reviewer. OVA emails follow the team's real, \
stakeholder-confirmed 4-zone template: LOGO -> HERO BANNER & CTA -> BODY CONTENT (a flexible, ordered list \
of content blocks) -> FOOTER & CLOSING CTA. You are demanding.

CANDIDATE FORMAT: "Subject:"/"Preheader:" lines, then "[LOGO]", the centred headline, subcopy, and \
"[CTA label]" button, then a "[HERO IMAGE: ...]" line (zone 2 - REQUIRED on every candidate, its absence is \
a hard-fail), then the BODY CONTENT zone: a variable-length sequence of blocks, each rendered as one of: \
plain paragraph lines, an "[IMAGE: ...]" line, an "=== section header ===" line, "[icon] caption" lines, an \
"[IMAGE (side): ...]" line followed by a heading/paragraph/icon lines, a "[CALLOUT CARD: heading - body]" \
line, "[check] item" lines, or a "[PRICE CARD: label | value | caption? | button]" line - THIS ZONE'S SHAPE AND LENGTH \
VARIES LEGITIMATELY BY EMAIL, never fail a candidate merely for having few blocks (a short logistics email) \
or several (a longer onboarding/educational email); judge each block's CONTENT against the rules below, not \
its presence. Then "[FOOTER & CLOSING CTA]" (zone 4, REQUIRED on every candidate - its absence, or a \
missing closing heading/bullets/button, is a hard-fail; a missing "[CLOSING IMAGE: ...]" line is NOT a \
hard-fail, that photo is optional), then a footer disclaimer and "Unsubscribe".

The price rule and the claim rule are PER-FLOW:
{price_rule}
{stat_rule}

CATEGORY FOR THIS CANDIDATE:
{category_notes}
Hard-fail any candidate that violates this category's own compliance notes above, in addition to every \
rule below.

A) COMPLIANCE (hard-fail any - OVA_SG_CRM_AGENT_SCOPE.md SS4):
1. Any prescription medicine or specific branded product NAMED IN THE WORDS anywhere - copy, block text, or \
the price card label. Describe the category and benefit only. This is a text/naming rule, not a reason to \
fail a real approved bank photo for merely showing a generic, unbranded product (a plain pill pack, an \
unlabelled vial, an injector pen) - the photo bank is already curated so none of its photos show a visible \
medicine name or brand; do not fail a hero/closing/block image on this rule unless it actually shows a real \
visible brand name or logo.
2. A discount PERCENTAGE quoted anywhere (a real dollar figure is fine when the price rule allows one, and \
IS expected there - do not fail a real, sourced dollar figure that correctly matches the flow brief).
3. Any medical advice, suitability assessment, dosing guidance, or symptom-matching. A care decision is \
made in consultation, never in an email.
4. A side-effect (or a symptom the patient might have) that is answered, reassured, or given a tip \
instead of being pointed to a doctor.
5. ANY number, percentage, or clinical statistic that is not one of this category's own real approved \
claims (see the category notes and the claim rule), copied verbatim. Fail a claim stated for the wrong \
category or paraphrased/upgraded from its real wording.
6. A price/dollar amount when the price rule forbids it, or a PRICE CARD whose value doesn't match the \
exact figure given.
7. Anything that could read as Malaysia-facing contraception content (including a Ringgit or non-SG \
currency), or any suggestion OVA prescribes or dispenses itself rather than connecting the patient to a \
licensed doctor/pharmacy.
8. Cure or guarantee language, shame, fear, or fake urgency.

B) BLOCK CONTENT - REAL BENEFITS ONLY (hard-fail any):
9. An "[icon] caption" line (icon_grid or two_column), or a "[check] item" line (checklist_card or the \
closing footer's bullets), stating a benefit that is NOT one of this category's real approved service \
benefits (see the category notes' "REAL, APPROVED SERVICE BENEFITS" list) or a plain factual/logistics \
step - a lightly reworded version of a real benefit is fine, a new invented one is not.
10. Any such short line stating a clinical claim/statistic (that belongs in a paragraph only, and only per \
the claim rule above).
11. A price_card block whose value doesn't match the flow's real price exactly, or that appears on a flow \
where the price/package decision is only a minor supporting detail, not the actual point of the email.
12. A price_card whose `value` is a full sentence or phrase (e.g. "Save S$137 versus paying month to \
month") instead of a short figure alone (e.g. "S$137") - value renders in large bold display text, so \
anything longer than a short price/saving figure is a hard-fail; the sentence belongs in `caption` instead.

C) VOICE - the four OVA pillars (smart not stuffy / calm authority / trustworthy not transactional / \
minimal and modern), hard-fail any:
- Salesy, pushy, or transactional tone; a hard sell; hype.
- Stuffy, academic, or jargon-heavy; OR cold and clinical; OR talking down / preaching.
- Emotional manipulation, guilt, or fear.
- Overpromising or exaggerating.
- Any em-dash or long dash; American spelling; exclamation marks; app-speak ("activate", "tap").
- The headline/button/card labels not in Title Case, or body copy not in sentence case.
- Defensive meta-commentary ("this isn't a sales email").
- Any internal business/marketing metric leaking into the copy.
- GENERIC COPY (a real, common failure - fail this on sight): a headline, subcopy, or body line that could \
be pasted unchanged into a different flow in this same category, or into any other telehealth brand's \
email, without anyone noticing. Good copy names the real, specific moment (what stage she's at, what \
decision is actually in front of her, what happens next) - if you cannot point to a specific phrase that \
proves this was written for THIS flow and no other, fail it.

D) FLOW FIDELITY (judge against the FLOW SPEC; hard-fail any):
{flow_spec}

- MIS-STAGING: any line contradicting the reader's actual state - referencing a consult, an order, or a \
decision she has not made in this flow; inventing a reason for her behaviour.
- The CTA button does not reasonably match the action this flow points to.
- EC-SPECIFIC (only if the flow spec is an emergency contraception flow): never ask what happened, never \
comment on timing or effectiveness - move straight to getting her to a doctor.
- NAME: any real-looking customer name in place of the literal NAME placeholder.
- HERO IMAGE (zone 2): REQUIRED on every candidate. CLOSING IMAGE (zone 4) and any block's own image are \
OPTIONAL - a genuine design choice, not a requirement (fail a candidate for HAVING one that doesn't fit, \
never for lacking one on a short/simple email; a text-only closing footer is correct and expected on plenty \
of emails). Whichever photos ARE present must genuinely fit this email's moment and category - a photo that \
reads as generic, forced, or mismatched (e.g. a weight-loss product shot on a contraception email), or one \
whose own description flags it as an imperfect/off-brand fit, is a fail. Heroes already used by OTHER \
touchpoints in this same flow are listed here: {other_heroes} - fail \
if the candidate's own top hero is an exact match to one of those names; a different hero is never a \
violation regardless of that list. Note: the Python-level check already hard-fails any duplicate image \
used TWICE WITHIN this same candidate (top hero, closing image, or a block's own image) before you ever \
see this candidate, so you do not need to re-check that yourself.

E) LEARNED CHECKS (from past real human feedback - same weight as the rules above):
{learned_rules}

For each failure write ONE short, specific reason describing what's actually wrong in the candidate. Set \
severity "none" if it passes, "minor" for small copy issues, "major" for any compliance (A), block-content \
(B), voice (C), flow-fidelity (D), or learned-check (E) failure.
"""

WHATSAPP_SYSTEM_PROMPT = """You are the Pre-Launch Sweeper for OVA Singapore - the last automated check \
before a WhatsApp touchpoint is shown to a human reviewer. This is a WhatsApp message, NOT an email.

CANDIDATE FORMAT: "[LINK PREVIEW: title]", a blank line, the message body (first line a short bold \
greeting/hook), a blank line, then EXACTLY ONE "[CTA label]" line. That is the real WABA Call-To-Action \
template - the "[LINK PREVIEW: ...]" and the one "[CTA label]" line are this renderer's stand-ins for the \
template's own fields; both are REQUIRED and CORRECT, never a violation. Only fail on CTA/link grounds if \
there are TWO OR MORE "[...]" CTA lines, or the body pastes a URL or names a second action.

The price rule and the claim rule are PER-FLOW:
{price_rule}
{stat_rule}

CATEGORY FOR THIS CANDIDATE:
{category_notes}
Hard-fail any candidate that violates this category's compliance notes, plus every rule below.

A) COMPLIANCE (hard-fail any):
1. Any prescription medicine or specific product named anywhere.
2. A discount percentage quoted anywhere.
3. Any medical advice, suitability assessment, dosing guidance, or symptom-matching.
4. A side-effect that is answered/reassured instead of pointed to a doctor.
5. ANY number/percentage/clinical statistic that is not a real approved claim, copied verbatim.
6. A price/dollar amount when the price rule forbids it, or one that doesn't match its exact figure.
7. Anything Malaysia-facing (contraception), or any suggestion OVA prescribes/dispenses itself.
8. Cure/guarantee language, shame, fear, or fake urgency.

B) SHAPE (WhatsApp, hard-fail any):
9. The message body (excluding the one CTA line) is longer than 4 short lines, or reads like a shrunk \
email (paragraphs, a list, a formal sign-off).
10. TWO OR MORE "[...]" CTA lines, or a second link/action named in the body.
11. Markdown (bold/italic asterisks, headings) or an image reference in the body.

C) VOICE (the four OVA pillars, hard-fail any):
- Salesy/pushy/transactional; hype; emotional manipulation.
- Stuffy/academic/jargon; OR cold/clinical; OR talking down.
- Any em-dash or long dash; American spelling; exclamation marks; app-speak.
- Defensive meta-commentary; the word "payment" or payment-led framing.
- Any internal business/marketing metric leaking in.

D) FLOW FIDELITY (judge against the FLOW SPEC; hard-fail any):
{flow_spec}

- EC-SPECIFIC (only if the flow spec is an emergency contraception flow): never ask what happened, never \
comment on timing or effectiveness.
- The "[CTA label]" does not reasonably match the action this flow points to.
- NAME: any real-looking customer name in place of NAME.

E) LEARNED CHECKS:
{learned_rules}

For each failure write ONE short, specific reason. Set severity "none"/"minor"/"major" (major for any \
compliance (A), shape (B), voice (C), flow-fidelity (D), or learned-check (E) failure).
"""


class SweeperResult(BaseModel):
    pass_: Literal["yes", "no"] = Field(alias="pass", description="'yes' if the email passes brand QA")
    reasons: List[str] = Field(default_factory=list, description="Specific reasons for any failure")
    severity: Literal["none", "minor", "major"] = Field(description="Overall severity of the issues found")

    class Config:
        populate_by_name = True


def _feedback_note(human_feedback: Optional[str]) -> str:
    if human_feedback:
        return (
            f"THE ACTUAL REVIEWER REQUEST THAT PRODUCED THIS CANDIDATE: {human_feedback.strip()}\n"
            "Use this to judge any LEARNED CHECK phrased as \"only when a reviewer explicitly asks\"."
        )
    return "This is a first draft, not a revision - no reviewer request produced it."


def _images_used_in_email(content: dict) -> List[str]:
    """Every real bank image key this one email actually uses: the top
    hero, the closing footer photo, and any block's own image
    (image_spotlight/two_column) - in that order. Skips 'none'/missing."""
    keys = []
    for k in (content.get("hero"), content.get("closing_image")):
        if k and k != "none":
            keys.append(k)
    for block in content.get("blocks") or []:
        img = block.get("image") if isinstance(block, dict) else getattr(block, "image", None)
        if img and img != "none":
            keys.append(img)
    return keys


def _hero_deterministic_issues(hero_info: Optional[dict], category: str = DEFAULT_CATEGORY) -> List[str]:
    """Python-level ground-truth checks the LLM shouldn't have to infer:
    is the top hero URL a real approved bank photo, never an invented
    placeholder (same reasoning as andSons' sweeper_agent.py) - AND, new
    for the 4-zone template, is every image used within this SAME email
    (top hero, closing photo, any block's own image) genuinely distinct
    from every other one, since a single email can now use several real
    bank photos at once and a duplicate would look like a rendering bug."""
    if not hero_info:
        return []
    issues = []
    hero = hero_info.get("hero")
    source = hero_info.get("hero_source")
    url = hero_info.get("hero_image_url")

    if source == "bank":
        bank_entry = HERO_BANK.get(hero)
        if bank_entry is None or url != bank_entry["url"]:
            issues.append(f"Hero '{hero}' claims to be a bank photo but its URL does not match the approved image bank - this looks like an invented or altered URL.")
        elif bank_entry.get("category") not in (category, "general") or category in bank_entry.get("exclude_categories", []):
            issues.append(f"Hero '{hero}' belongs to a different category's photo bank than this email's '{category}' - a real content mismatch.")
    elif source == "none":
        if url:
            issues.append("Hero is marked 'none' but an image URL is still attached.")
        if hero_bank_for_category(category):
            issues.append(
                "Hero is 'none' but this template requires a hero image on every email, and this "
                "category has real bank options available - 'none' is a last-resort escape hatch, "
                "not a normal choice, and should not fire here."
            )
    else:
        issues.append(f"Hero source '{source}' is not a real approved bank photo - selection is bank-only.")

    closing_image = hero_info.get("closing_image")
    if closing_image and closing_image != "none":
        closing_entry = HERO_BANK.get(closing_image)
        if closing_entry is None:
            issues.append(f"closing_image '{closing_image}' is not a real approved bank photo - selection is bank-only.")
        elif closing_entry.get("category") not in (category, "general") or category in closing_entry.get("exclude_categories", []):
            issues.append(f"closing_image '{closing_image}' belongs to a different category's photo bank than this email's '{category}' - a real content mismatch.")

    used = _images_used_in_email(hero_info)
    seen = {}
    for key in used:
        seen[key] = seen.get(key, 0) + 1
    duplicates = [k for k, n in seen.items() if n > 1]
    if duplicates:
        issues.append(
            f"The same real bank photo ({', '.join(duplicates)}) is used more than once within this "
            "SAME email (top hero, closing photo, and/or a block's own image must all be distinct from "
            "each other) - pick a different, genuinely fitting photo for each spot instead."
        )

    return issues


def sweep_email(
    email_text: str, flow_name: str = "contraception_consult_reminder", hero_info: Optional[dict] = None,
    other_heroes: Optional[List[str]] = None, human_feedback: Optional[str] = None,
    category: str = DEFAULT_CATEGORY, **_ignored,
) -> dict:
    flow = FLOW_BY_SLUG.get(flow_name, {})
    llm = get_llm("SWEEPER")
    structured_llm = llm.with_structured_output(SweeperResult)

    system_text = SYSTEM_PROMPT.format(
        price_rule=_price_rule_text(flow), stat_rule=_stat_rule_text(flow, category),
        learned_rules=learned_rules_text(), flow_spec=_build_flow_brief(flow_name, category),
        category_notes=category_notes_text(category),
        other_heroes=", ".join(other_heroes) if other_heroes else "(none - this is the only email touchpoint, or the first one)",
    )
    human_text = f"Flow: {flow_name}\n{_feedback_note(human_feedback)}\n\nCandidate email:\n---\n{email_text}\n---"

    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm

    result, _ = invoke_with_retry(chain, label="Sweeper structured-output call")
    if result is None:
        return {"pass": False, "reasons": ["Automated brand QA check failed to run (technical error) - needs human review before sending."], "severity": "major"}

    reasons = list(result.reasons)
    passed = result.pass_ == "yes"
    severity = result.severity

    hero_issues = _hero_deterministic_issues(hero_info, category)
    if hero_issues:
        reasons = hero_issues + reasons
        passed = False
        severity = "major"

    return {"pass": passed, "reasons": reasons, "severity": severity}


def sweep_whatsapp(
    message_text: str, flow_name: str = "contraception_consult_reminder", human_feedback: Optional[str] = None,
    category: str = DEFAULT_CATEGORY, **_ignored,
) -> dict:
    flow = FLOW_BY_SLUG.get(flow_name, {})
    llm = get_llm("SWEEPER")
    structured_llm = llm.with_structured_output(SweeperResult)

    system_text = WHATSAPP_SYSTEM_PROMPT.format(
        price_rule=_price_rule_text(flow), stat_rule=_stat_rule_text(flow, category),
        learned_rules=learned_rules_text(), flow_spec=_build_flow_brief(flow_name, category),
        category_notes=category_notes_text(category),
    )
    human_text = f"Flow: {flow_name}\n{_feedback_note(human_feedback)}\n\nCandidate WhatsApp message:\n---\n{message_text}\n---"

    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    chain = prompt | structured_llm

    result, _ = invoke_with_retry(chain, label="WhatsApp Sweeper structured-output call")
    if result is None:
        return {"pass": False, "reasons": ["Automated brand QA check failed to run (technical error) - needs human review before sending."], "severity": "major"}

    return {"pass": result.pass_ == "yes", "reasons": list(result.reasons), "severity": result.severity}

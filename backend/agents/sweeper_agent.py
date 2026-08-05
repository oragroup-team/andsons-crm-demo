"""Sweeper agent - the Pre-Launch brand-QA gate for andSons CRM emails.

Input: rendered email text + flow_name. Output: {pass, reasons, severity}.
Enforces the real andSons compliance rulebook and golden-standard checks
(source: CRM_Email_Generation_Data/&SONS CRM Knowledge,
02-Compliance-Claims.md "Sweeper checklist" + the live Pre-Launch Sweeper
agent prompt's golden-standard hard-fail checks).
"""
from typing import List, Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from flows import FLOW_BY_SLUG
from image_bank import HERO_BANK

from .copywriter_agent import GOLDEN_P1_REFERENCE
from .llm_provider import get_llm

SYSTEM_PROMPT = """You are the Pre-Launch Sweeper for andSons - the last automated QA check before an \
email is shown to a human reviewer. You review against THREE dimensions and are demanding; your job is \
to have an eye for every detail so nothing has to be caught later.

Golden template (P1 "mixed style" - the quality floor for restraint, register, and structure that every \
flow is measured against, not a literal script every flow must copy):
---
{golden_reference}
---

You will always be told the candidate's flow and its track (rx / otc / neutral) in the human message. \
The price rule is PER-FLOW - read it carefully, it is the single most common mistake to get wrong:
{price_rule}

A) COMPLIANCE (hard-fail any of these):
1. Any prescription medicine named anywhere (Minoxidil, Finasteride, or any drug name) - Rx treatment \
must only ever be "your doctor's plan" / "treatment plan" / "prescription options" / "doctor-guided \
treatment".
2. Treatment decisions not attributed to the doctor, or the brand speaking as if it prescribes.
3. Any claim or implication the customer can contact/message the doctor directly - support is customer \
service via WhatsApp only.
4. Rx-track copy implying the customer can self-stop or self-change prescribed treatment.
5. A clinical stat, percentage, or claim without BOTH the source footnote (DOI: 10.1111/dth.12246) AND \
the sentence "Individual results vary." Also fail any invented/unverifiable statistic, rating, or social \
proof number - even with a footnote format, a number not grounded in the real approved claim is fabricated.
6. A price, dollar amount, or discount code mentioned when the flow's track forbids it (see the per-flow \
rule above).
7. Missing a working footer: WhatsApp customer-service line, "andSons Pte. Ltd., 1 Fusionopolis Place, \
#17-10, Galaxis, Singapore 138522", and an unsubscribe line must all be present. The footer must NOT \
contain "Manage My Delivery Schedule" or "Cancel Anytime" links.
8. Cure/guarantee language ("cure baldness", "guaranteed regrowth", "100% works"), shame or fear-based \
pressure, or fake urgency/countdown framing.

HERO IMAGE (when a "[HERO IMAGE: ...]" block is present at the top of the candidate, before Subject):
- A deliberate no-hero, text-first email is a VALID choice, never a failure by itself.
- The hero, when used, must be a real approved bank photo - never a CSS shape, icon, badge, \
illustration, or star-rating graphic standing in for one.
- The hero must genuinely fit THIS email's message and moment; a hero that reads as generic, forced, or \
mismatched to the email's content (e.g. a routine/grooming photo on a doctor-consult reminder) is a fail.
- An "overlay headline" (when present) must be short, plain, and concrete (2-5 words), never poetic or \
abstract wordplay - but a "baked-in headline" is different: it's text already part of the approved \
image file itself, not something the Copywriter wrote, so it is NOT subject to the 2-5 word rule or \
any wording check - never fail an email for a baked-in headline's length or phrasing.
- The hero block must appear first, before Subject/Preheader, when present.
- At most one hero per email (this is enforced upstream, but flag it if you somehow see more than one).

B) POLISH & CORRECTNESS:
- Brand name written exactly "andSons" (never "&Sons").
- Sentence case throughout; no awkward auto-text ("Dear there,").
- Signature is exactly "The andSons team" with no dash before it.
- Subject reasonably short and compelling; preheader present.
- At most ONE hero image reference; at most ONE numbered/list-style content block (never two separate \
"what happens next" style lists in the same email). A trust line is a SEPARATE, always-thin element and \
does NOT count against that limit - the approved golden template uses a numbered list AND a trust line \
together, so having both is correct, not a violation. A trust row must be a thin plain-text line \
separated by " · ", never described as badge graphics, icons, or star ratings.
- The "what happens next" block (when present) has at most 3 short lines, not long paragraphs, and is \
not duplicated.

C) REGISTER & CREATIVE STRENGTH (hard-fail any):
- SaaS/app language applied to medical care: "activate", "tap", "unlock", exclamation marks, cutesy \
paired lines. A plan is "confirmed" or "reviewed", never "activated".
- CTA does not follow the "verb + My + noun" convention (e.g. "Start My Treatment", "Book My Free \
Consultation").
- Register is cutesy/SaaS OR stiff-corporate/clinical instead of the Juniper target: personal, warm, \
plainspoken, short one-line paragraphs.
- Any em-dash or long dash used as punctuation (must be a full stop or comma instead).
- American spelling anywhere (must be British: personalised, customised, recognise, colour, programme).
- Any DEFENSIVE META-COMMENTARY narrating the email's intent or what it is NOT doing ("we're not selling \
you anything", "this isn't a sales pitch", "the only reason we're reaching out").
- The word "payment" appearing in the subject, preheader, or CTA, or payment-led framing anywhere (the \
action is starting/continuing treatment, not paying) - UNLESS the flow is genuinely a billing flow \
(Replenishment/Dunning), where a plain, guilt-free mention in the body is acceptable.
- Any implication that days have passed since the underlying trigger event when the flow can plausibly \
send same-day ("taking a few days", "you've been thinking it over").

OPTIONAL BLOCKS - DO NOT FAIL FOR OMISSION: the "what happens next" list, the trust line, and the gentle \
truth line are judgement calls the Copywriter makes per email. An email that omits one or all of them is \
NOT a failure by itself - only fail if a block IS present and is done wrong (more than 3 steps, more than \
one hero, badge graphics, illustrated icons, duplicated blocks, etc).

For each failure, write ONE short, specific reason describing what's actually wrong in the candidate (not \
the rule text verbatim). Set severity to "none" if it passes, "minor" for small copy/polish issues, or \
"major" for any compliance (A) failure or structural/brand-safety violation.
"""


def _price_rule_text(flow_name: str) -> str:
    flow = FLOW_BY_SLUG.get(flow_name)
    if flow is None:
        return (
            "Unknown flow - treat conservatively: FAIL any price, dollar amount, or discount code "
            "unless it is clearly grounded in the approved OTC catalogue (Redensyl serum $42, Intense "
            "Hair Growth Trio $78, Intense Hair Growth Kit $78)."
        )
    if flow["allow_price"]:
        return (
            f"Flow = {flow_name} ({flow['label']}, {flow['track']} track) -> a real price from the "
            "approved OTC catalogue (Redensyl serum $42, Intense Hair Growth Trio $78, Intense Hair "
            "Growth Kit $78) is PERMITTED here, but it is OPTIONAL, not required. Do NOT fail the email "
            "for mentioning one of these real prices - that is the flow working as intended. Do NOT fail "
            "the email for omitting a price either - a price-free version of this email is equally "
            "valid; whether to include one is the Copywriter's judgement call. Only fail if a price IS "
            "present and does not match the approved catalogue (a fabricated price)."
        )
    return (
        f"Flow = {flow_name} ({flow['label']}, {flow['track']} track) -> a price, dollar amount, or "
        "discount code is FORBIDDEN. This flow must stay no-payment / no-price."
    )


def _hero_deterministic_issues(hero_info: Optional[dict]) -> List[str]:
    """Python-level ground-truth checks the LLM shouldn't have to infer: is
    the hero URL a real approved bank photo, never an invented placeholder,
    and is a baked hero free of a redundant headline. Selection is
    bank-only, so any non-bank, non-"none" source is itself a fail."""
    if not hero_info:
        return []
    issues = []
    hero = hero_info.get("hero")
    source = hero_info.get("hero_source")
    url = hero_info.get("hero_image_url")

    if source == "bank":
        bank_entry = HERO_BANK.get(hero)
        if bank_entry is None or url != bank_entry["url"]:
            issues.append(
                f"Hero '{hero}' claims to be a bank photo but its URL does not match the approved "
                "image bank - this looks like an invented or altered URL."
            )
        elif bank_entry["baked_headline"] is not None and hero_info.get("hero_headline") not in (
            None, bank_entry["baked_headline"],
        ):
            issues.append(
                f"Hero '{hero}' already has a baked-in headline but a different overlay headline "
                "was added on top of it."
            )
    elif source == "none":
        if url:
            issues.append("Hero is marked 'none' but an image URL is still attached.")
    else:
        issues.append(f"Hero source '{source}' is not a real approved bank photo - selection is bank-only.")

    return issues


class SweeperResult(BaseModel):
    pass_: bool = Field(alias="pass", description="True if the email passes brand QA")
    reasons: List[str] = Field(default_factory=list, description="Specific reasons for any failure")
    severity: Literal["none", "minor", "major"] = Field(
        description="Overall severity of the issues found"
    )

    class Config:
        populate_by_name = True


def sweep_email(email_text: str, flow_name: str = "p1_plan_not_purchased", hero_info: Optional[dict] = None) -> dict:
    llm = get_llm("SWEEPER")
    structured_llm = llm.with_structured_output(SweeperResult)

    system_text = SYSTEM_PROMPT.format(
        golden_reference=GOLDEN_P1_REFERENCE,
        price_rule=_price_rule_text(flow_name),
    )
    human_text = f"Flow: {flow_name}\n\nCandidate email:\n---\n{email_text}\n---"

    prompt = ChatPromptTemplate.from_messages(
        [("system", system_text), ("human", human_text)]
    )
    chain = prompt | structured_llm
    result: SweeperResult = chain.invoke({})

    reasons = list(result.reasons)
    passed = result.pass_
    severity = result.severity

    hero_issues = _hero_deterministic_issues(hero_info)
    if hero_issues:
        reasons = hero_issues + reasons
        passed = False
        severity = "major"

    return {
        "pass": passed,
        "reasons": reasons,
        "severity": severity,
    }

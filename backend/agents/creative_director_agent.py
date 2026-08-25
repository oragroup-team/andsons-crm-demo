"""Email Creative Director agent - the real n8n pipeline's second step
(source: "Agent Prompts - CRM Team.md", node 3: Email Creative Director).

The Copywriter writes the words and proposes a hero as part of the same
pass (a pragmatic simplification of the real two-step split - see the
module docstring note below); this agent is a genuine SECOND, independent
pass that reviews and can OVERRIDE the art direction - hero choice,
overlay headline, and layout - never the copy itself. It runs after the
Copywriter and before the Sweeper, exactly where the real pipeline puts
it, enforcing the real HERO UNIQUENESS hard rule (never reuse a hero
already used elsewhere in the same flow) as its own independent check,
not just trusting the Copywriter's own self-restraint.

Real system note this demo simplifies: in the live n8n workflow, the
Copywriter never picks a hero at all - Creative Director makes that call
from a blank slate. Here, the Copywriter still proposes one (so a single
LLM failure doesn't leave a touchpoint without ANY visual direction to
review), and the Creative Director's job is to independently confirm or
override it - a real second opinion, not a rubber stamp.
"""
import logging
from typing import List, Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from categories import DEFAULT_CATEGORY
from image_bank import HERO_BANK, HERO_KEYS, hero_bank_for_category

from .llm_provider import get_llm, invoke_with_retry

logger = logging.getLogger("creative_director_agent")

SYSTEM_PROMPT = """You are the EMAIL CREATIVE DIRECTOR for andSons Singapore (men's health telehealth). \
The copywriter has already written the words below - you do NOT rewrite the copy. You own the art \
direction: which hero image (if any), and whether an optional short display headline sits above the body.

HERO UNIQUENESS (HARD RULE): NEVER reuse a hero image that appears anywhere else in this flow - the list \
of heroes already used elsewhere in this flow is given to you below; if the copywriter's proposed hero is \
already on that list, you MUST override it with a different one, or 'none'. A weak or ill-fitting hero is \
worse than none - do not force a mismatch just to have an image.

APPROVED PHOTO BANK:
{hero_catalog}

- "smiling" and "adjusting" are LOCKED heroes with a headline already baked into the image file - never \
set hero_headline for these.
- Every other key is a RAW photo with no baked text - if you use one, hero_headline should be a short, \
CONCRETE line (2-5 words) a real person would actually say, never a formal/clinical noun phrase.
- headline (separate from hero_headline) is an OPTIONAL short display line (<=6 words) shown above the \
body copy, independent of any hero photo - leave it empty unless it genuinely strengthens this moment; \
most emails don't need one.
- Invent nothing: only approved bank keys, never a new image.

THE COPY (do not change any of this, only decide the visuals around it):
Subject: {subject}
Opening: {opening_line}

COPYWRITER'S PROPOSED HERO: {proposed_hero} (headline: {proposed_headline!r})

{prior_context}
"""


class CreativeDirection(BaseModel):
    hero: Literal[tuple(HERO_KEYS)] = Field(
        description="The FINAL hero choice for this touchpoint - confirm the copywriter's proposal or "
        "override it, one of the approved bank keys, or 'none'."
    )
    hero_headline: Optional[str] = Field(
        default=None,
        description="Short overlay headline (2-5 words) for a RAW bank photo. Null for locked heroes "
        "('smiling'/'adjusting') and for 'none'.",
    )
    headline: Optional[str] = Field(
        default=None,
        description="OPTIONAL short display headline (<=6 words) shown above the body copy, independent "
        "of the hero. Null for most emails - only when it genuinely strengthens this moment.",
    )
    art_rationale: str = Field(description="One short line: why this hero (or 'none') fits this specific moment.")


def _hero_catalog_text(category: str = DEFAULT_CATEGORY) -> str:
    """Category-scoped, same reasoning as copywriter_agent._build_hero_
    catalog() - showing the Creative Director the FULL bank regardless of
    category would let it override a Copywriter's correct 'none' choice
    with a Hair-Loss-only photo on a Weight-Loss/ED-PE/Skin email."""
    bank = hero_bank_for_category(category)
    lines = []
    for key, entry in bank.items():
        locked = " [LOCKED]" if entry["baked_headline"] else ""
        lines.append(f'- "{key}"{locked}: {entry["description"]}. Best for: {entry["moment"]}.')
    if not lines:
        return "(No reviewed photos exist for this category yet - always choose \"none\".)"
    return "\n".join(lines)


def direct_touchpoint(content: dict, prior_summaries: List[dict], category: str = DEFAULT_CATEGORY) -> dict:
    """Reviews one email touchpoint's copy + the Copywriter's proposed
    hero, and returns the FINAL art-direction decision (hero,
    hero_headline, headline, art_rationale). Fails safe: if the LLM call
    can't complete, keeps the Copywriter's original proposal rather than
    blocking the pipeline on an art-direction failure - a text-first
    fallback (dropping to 'none') would be worse than trusting a
    reasonable first-pass choice that was never independently reviewed."""
    used_heroes = [p["hero"] for p in prior_summaries if p.get("hero") and p["hero"] != "none"]
    prior_context = (
        f"Heroes already used elsewhere in this flow: {', '.join(used_heroes)} - do not reuse any of these."
        if used_heroes else "No earlier touchpoint in this flow has used a hero yet."
    )

    llm = get_llm("CREATIVE_DIRECTOR", temperature=0.4)
    structured_llm = llm.with_structured_output(CreativeDirection)
    system_text = SYSTEM_PROMPT.format(
        hero_catalog=_hero_catalog_text(category),
        subject=content.get("subject", ""),
        opening_line=(content.get("opening_lines") or [""])[0],
        proposed_hero=content.get("hero", "none"),
        proposed_headline=content.get("hero_headline"),
        prior_context=prior_context,
    )
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", "Make the art direction call.")])
    chain = prompt | structured_llm

    result, last_exc = invoke_with_retry(chain, label="Creative Director structured-output call")
    if result is None:
        logger.warning(
            "Creative Director failed after retrying (%s) - keeping the Copywriter's proposed hero %r unreviewed.",
            last_exc, content.get("hero"),
        )
        return {
            "hero": content.get("hero", "none"),
            "hero_headline": content.get("hero_headline"),
            "headline": None,
            "art_rationale": "(Creative Director review unavailable - Copywriter's original choice kept as-is.)",
        }

    return result.model_dump()

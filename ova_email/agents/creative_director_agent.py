"""Email Creative Director agent - a genuine second, independent pass over
the Copywriter's proposed hero image, ported mechanically from the andSons
backend's agents/creative_director_agent.py (same role, same HERO
UNIQUENESS hard rule, same "never rewrite the copy" boundary) - restored
for OVA per explicit instruction to follow the same hero-image-selection
process andSons uses, now that a real OVA photo bank exists
(image_bank.py, curated from `01_OVA_Rebrand_2025`).

The Copywriter proposes a hero as part of its own pass (so one LLM failure
never leaves a touchpoint with zero visual direction); this agent
independently confirms or overrides that choice - never the copy itself -
and is the one place hero-uniqueness across a flow's touchpoints is
enforced as a real second opinion, not just the Copywriter's own restraint.
"""
import logging
from typing import List, Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from categories import DEFAULT_CATEGORY
from image_bank import HERO_BANK, HERO_KEYS, hero_bank_for_category

from .llm_provider import get_llm, invoke_with_retry

logger = logging.getLogger("creative_director_agent")

SYSTEM_PROMPT = """You are the EMAIL CREATIVE DIRECTOR for OVA Singapore (women's health telehealth). The \
doctor has already written the words below - you do NOT rewrite the copy. You own one decision: which real \
hero image (if any) actually fits this moment.

HERO UNIQUENESS (HARD RULE): NEVER reuse a hero image that appears anywhere else in this flow - the list \
of heroes already used elsewhere in this flow is given below; if the proposed hero is already on that \
list, override it with a different one, or 'none'. A weak or ill-fitting hero is worse than none - never \
force a mismatch just to have an image.

APPROVED PHOTO BANK:
{hero_catalog}

- Every key is a REAL photo/product render, described exactly as it is - never invent what one shows.
- Invent nothing: only approved bank keys, never a new image.

THE COPY (do not change any of this, only decide the visual):
Subject: {subject}
Opening: {opening_line}

PROPOSED HERO: {proposed_hero}

{prior_context}
"""


class CreativeDirection(BaseModel):
    hero: Literal[tuple(HERO_KEYS)] = Field(
        description="The FINAL hero choice for this touchpoint - confirm the proposal or override it, one of the approved bank keys, or 'none'."
    )
    art_rationale: str = Field(description="One short line: why this hero (or 'none') fits this specific moment.")


def _hero_catalog_text(category: str = DEFAULT_CATEGORY) -> str:
    """Category-scoped, same reasoning as copywriter_agent._build_hero_
    catalog() - showing the full bank regardless of category would let this
    override a correct 'none' with a photo that belongs to a different
    category's moment."""
    bank = hero_bank_for_category(category)
    lines = []
    for key, entry in bank.items():
        lines.append(f'- "{key}": {entry["description"]}. Best for: {entry["moment"]}.')
    if not lines:
        return "(No reviewed photos exist for this category yet - always choose \"none\".)"
    return "\n".join(lines)


def direct_touchpoint(content: dict, prior_summaries: List[dict], category: str = DEFAULT_CATEGORY) -> dict:
    """Reviews one email touchpoint's copy + the Copywriter's proposed
    hero, and returns the FINAL art-direction decision (hero,
    art_rationale). Fails safe: if the LLM call can't complete, keeps the
    Copywriter's original proposal rather than blocking the pipeline."""
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
        opening_line=content.get("intro", ""),
        proposed_hero=content.get("hero", "none"),
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
        return {"hero": content.get("hero", "none"), "art_rationale": "(Creative Director review unavailable - Copywriter's original choice kept as-is.)"}

    return result.model_dump()

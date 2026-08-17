"""Skill Distiller agent - the real n8n pipeline's continuous-learning step
(source: "andSons CRM — Project Overview & Runbook.md", section 5: "Skill
Distiller | Turns each Tom comment into a reusable rule in Learned_Rules").

Every other agent in this system starts every session from zero - no
memory of any past human correction. This is the one piece that closes
that gap: after a human gives real revision feedback, this turns it into
a short, generalizable standing rule (not just a record of that one
edit), stored in Firestore, and read by Head of CRM/Copywriter/Sweeper on
every future run - so a correction given once never has to be given
again for the same mistake in a different flow.
"""
import logging
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from session_store import add_learned_rule, get_learned_rules

from .llm_provider import get_llm, invoke_with_retry

logger = logging.getLogger("learned_rules_agent")

SYSTEM_PROMPT = """A human reviewer just gave real feedback on one specific andSons CRM touchpoint. Turn \
it into ONE short, general, reusable standing rule future drafts should follow - not a record of this one \
edit, a rule that would have prevented needing this feedback in the first place, phrased so it applies \
beyond just this one touchpoint.

If the feedback is genuinely too specific to generalize (a one-off preference about this exact touchpoint, \
not a repeatable pattern), say so plainly instead of forcing a fake general rule.

THE FEEDBACK: {feedback}

THE TOUCHPOINT IT WAS ABOUT (for context only - do not quote it back, distill the underlying rule):
{context}
"""


class DistilledRule(BaseModel):
    generalizable: bool = Field(description="True if this feedback reflects a real repeatable pattern "
                                 "worth turning into a standing rule. False if it's a one-off preference "
                                 "specific to this exact touchpoint that wouldn't apply elsewhere.")
    rule: Optional[str] = Field(
        default=None,
        description="If generalizable: the short, standalone standing rule, phrased as an instruction "
        "future drafts should follow (e.g. 'CTA buttons default to left-aligned unless a reviewer asks "
        "to centre one'). Null if not generalizable.",
    )


def distill_and_save_rule(feedback: str, touchpoint_context: str) -> Optional[str]:
    """Turns one piece of real human feedback into a standing rule and
    saves it to Firestore. Best-effort: never raises - a failed distill
    just means this one correction doesn't carry forward, which is no
    worse than the system's behaviour before this agent existed. Returns
    the saved rule text, or None if nothing was saved."""
    llm = get_llm("HEAD_OF_CRM", temperature=0.3)  # same provider tier as the other strategy-level agents
    structured_llm = llm.with_structured_output(DistilledRule)
    system_text = SYSTEM_PROMPT.format(feedback=feedback, context=touchpoint_context[:500])
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", "Distil this into a rule, or say it doesn't generalize.")])
    chain = prompt | structured_llm

    result, last_exc = invoke_with_retry(chain, attempts=2, label="Skill Distiller structured-output call")
    if result is None:
        logger.warning("Skill Distiller failed after retrying (%s) - this feedback won't become a standing rule.", last_exc)
        return None
    if not result.generalizable or not result.rule:
        logger.info("Skill Distiller: feedback %r judged too specific to generalize - not saved.", feedback[:80])
        return None

    add_learned_rule(result.rule)
    logger.info("New standing rule learned: %r", result.rule)
    return result.rule


def learned_rules_text() -> str:
    """Plain-text block for injection into any agent's prompt - every
    standing rule learned so far, or a clear 'none yet' when empty."""
    rules = get_learned_rules()
    if not rules:
        return "(none recorded yet)"
    return "\n".join(f"- {r}" for r in rules)

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
it into ONE short, reusable standing rule future drafts should follow - not a record of this one edit.

BE CONSERVATIVE ABOUT SCOPE (the most common real mistake here): a request to CHANGE something away from \
an established default (a layout choice, a design convention, an approved template's own structure, \
adding a specific detail like a number or a fact) is almost always a one-off preference for THIS \
touchpoint, not a new universal default - distil it as a CONDITIONAL rule ("only do X when a reviewer \
explicitly asks for it"), never as an unconditional new default ("always do X now"), unless the feedback \
itself says something is ALWAYS wrong (a genuine compliance/register/factual mistake - those generalize \
unconditionally). When in doubt between a conditional rule and an unconditional one, pick the conditional \
one - a future draft silently changing an approved default because of one person's one-time request is \
worse than a rule that's phrased too narrowly.
Concrete example of this exact mistake: a reviewer once asked for a real customer-count number to be \
worked into ONE specific email as social proof. The WRONG distillation was "always include a social-proof \
number as the first bullet when one is available" (turns a one-off creative choice into a universal \
default that will force a stat into every future email whether anyone asked for one or not) - the RIGHT \
distillation is "only add a social-proof number when a reviewer explicitly asks for one in that specific \
touchpoint's feedback".

NEVER bake a specific literal detail from this one feedback - a number, a name, a quoted sentence, an \
exact phrase - into the rule text as if it were a reusable template. A future draft reading this rule must \
never copy that literal detail verbatim into an unrelated email; state the PATTERN only (e.g. "insert a \
real number the reviewer actually supplies", never "insert '1,289+ men...'").

EXISTING STANDING RULES (do not add a new rule that duplicates or near-duplicates one of these - if this \
feedback is already covered, say not generalizable instead of adding a redundant near-copy):
{existing_rules}

If the feedback is genuinely too specific to generalize at all (nothing repeatable, not even as a \
conditional), say so plainly instead of forcing a fake rule.

THE FEEDBACK: {feedback}

THE TOUCHPOINT IT WAS ABOUT (for context only - do not quote it back, distill the underlying rule):
{context}
"""


class DistilledRule(BaseModel):
    generalizable: bool = Field(description="True if this feedback reflects a real repeatable pattern "
                                 "worth turning into a standing rule, genuinely distinct from every "
                                 "existing rule already listed. False if it's a one-off preference "
                                 "specific to this exact touchpoint, or if an existing rule already "
                                 "covers this pattern.")
    rule: Optional[str] = Field(
        default=None,
        description="If generalizable: the short, standalone standing rule, phrased as an instruction "
        "future drafts should follow, stating the PATTERN only - never a specific number/name/quoted "
        "phrase from this one piece of feedback (e.g. 'insert a real number the reviewer actually "
        "supplies when they ask for social proof', never 'insert 1,289+ men...'). Null if not "
        "generalizable.",
    )


def distill_and_save_rule(feedback: str, touchpoint_context: str) -> Optional[str]:
    """Turns one piece of real human feedback into a standing rule and
    saves it to Firestore. Best-effort: never raises - a failed distill
    just means this one correction doesn't carry forward, which is no
    worse than the system's behaviour before this agent existed. Returns
    the saved rule text, or None if nothing was saved."""
    existing_rules = get_learned_rules()
    existing_rules_text = "\n".join(f"- {r}" for r in existing_rules) if existing_rules else "(none yet)"

    llm = get_llm("HEAD_OF_CRM", temperature=0.3)  # same provider tier as the other strategy-level agents
    structured_llm = llm.with_structured_output(DistilledRule)
    system_text = SYSTEM_PROMPT.format(
        feedback=feedback, context=touchpoint_context[:500], existing_rules=existing_rules_text
    )
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

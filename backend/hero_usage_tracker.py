"""Cross-call, cross-instance memory of which hero images have actually
been picked recently, per category - the real fix for a live stakeholder
complaint ("hero selection is biased, very poor"): every single hero
decision (copywriter_agent.generate_email/generate_flow_email_touchpoint,
creative_director_agent.direct_touchpoint) previously started from a blank
slate every single call, with NO memory of what was chosen a minute ago,
an hour ago, or in a different Slack thread entirely. The only existing
anti-repeat mechanism (_prior_touchpoints_context in copywriter_agent.py)
only looks at touchpoints *within the same multi-step flow generation* -
it has nothing to say about the far more common case of many independent
single-email requests (one-off Slack asks, separate flows, different
days). hero_bank_for_category()'s random.shuffle() only fixes LIST-
POSITION bias (always picking whatever's listed first) - it does nothing
about a model's own semantic preference for the same "safest-sounding"
one or two photos, which is a well-documented, separate failure mode and
a much better fit for what "the same image keeps showing up" looks like
in practice.

Firestore REST, same pattern (and the same hard-won reasons) as
session_store.py: Cloud Run can run multiple concurrent instances and
replaces them entirely on every deploy, so anything module-level in
Python memory is invisible across requests and wiped on every redeploy -
exactly the class of bug session_store.py's own docstring documents for
session state, and exactly why a local/in-memory counter here would look
like it works in a quick manual test and then silently stop doing
anything useful in real production traffic.

Schema: one document per real category (categories.VALID_CATEGORY_SLUGS)
in the `hero_usage` collection, holding a single `recent` field - a plain
list of hero keys in the order they were picked, oldest first. Capped at
_MAX_STORED entries on write (oldest evicted) so the document never grows
unbounded; the prompt-facing helper below only ever surfaces the last
_RECENCY_WINDOW of those, which is the number of picks that actually still
feels "recent" to a human reviewing output, not the full history.
"""
import logging
from typing import List

from session_store import get_doc as _get_doc, set_doc as _set_doc

logger = logging.getLogger("hero_usage_tracker")

_COLLECTION = "hero_usage"
_MAX_STORED = 60
_RECENCY_WINDOW = 12


def _doc_id(category: str) -> str:
    return category or "unknown"


def get_recent_heroes(category: str, limit: int = _RECENCY_WINDOW) -> List[str]:
    """The last `limit` hero keys actually picked for this category, most
    recent LAST (so a human skimming the list reads it in the order it
    happened). Fails soft (empty list) on any Firestore error - a missing
    recency signal should degrade to the old "no memory" behaviour, never
    block or crash a real generation."""
    try:
        doc = _get_doc(_COLLECTION, _doc_id(category))
        recent = (doc or {}).get("recent", [])
        return recent[-limit:]
    except Exception:
        logger.exception("Failed to read recent hero usage for category %r - proceeding with no recency signal.", category)
        return []


def record_hero_use(category: str, hero_key: str) -> None:
    """Appends one real, final hero decision to this category's history.
    Deliberately skips 'none' and empty/null values - a deliberate
    text-first choice is not a hero to avoid repeating, and recording it
    would only dilute the real signal with noise."""
    if not hero_key or hero_key == "none":
        return
    try:
        doc = _get_doc(_COLLECTION, _doc_id(category))
        recent = (doc or {}).get("recent", [])
        recent.append(hero_key)
        _set_doc(_COLLECTION, _doc_id(category), {"recent": recent[-_MAX_STORED:]})
    except Exception:
        logger.exception("Failed to record hero use (%r, %r) - this pick won't influence future recency bias.", category, hero_key)


def recent_heroes_prompt_block(category: str) -> str:
    """Human-readable instruction block to append to a hero-catalog prompt
    section - real counts, not just a bare list, so repeated favourites
    are visibly called out rather than blending in with a one-off pick
    from an hour ago."""
    recent = get_recent_heroes(category)
    if not recent:
        return ""
    counts: dict = {}
    for key in recent:
        counts[key] = counts.get(key, 0) + 1
    overused = sorted((k for k, c in counts.items() if c >= 3), key=lambda k: -counts[k])
    lines = [
        "",
        "",
        f"RECENTLY USED ACROSS OTHER EMAILS IN THIS CATEGORY (most recent last, last {len(recent)} picks): "
        + ", ".join(recent) + ".",
        "This spans separate requests and flows, not just this one - real evidence of what's actually been "
        "shown to customers lately, not a hypothetical. Treat heavy repeats here as a real problem to correct, "
        "not a safe default to continue: deliberately favour a photo that is rare or absent from this list "
        "whenever it genuinely fits the moment, over one that keeps reappearing - variety across the bank is "
        "itself part of a good choice here, not a tiebreaker.",
    ]
    if overused:
        lines.append(
            "Especially overused lately, avoid unless genuinely the only real fit: "
            + ", ".join(f"{k} ({counts[k]}x)" for k in overused) + "."
        )
    return "\n".join(lines)

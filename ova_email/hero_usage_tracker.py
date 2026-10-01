"""Cross-call, cross-instance memory of which hero images have actually
been picked recently, per category - same real fix, same reasoning, as
backend/hero_usage_tracker.py (andSons' equivalent service): every single
hero decision previously started from a blank slate every call, with no
memory of what was chosen a minute ago or in a different Slack thread.
hero_bank_for_category()'s random.shuffle() only fixes LIST-POSITION bias
- it says nothing about a model's own semantic preference for the same
one or two "safest-sounding" photos, which is a separate, well-documented
failure mode and a much better fit for a real "the same image keeps
showing up" complaint.

Firestore REST, via session_store.py's generic get_doc/set_doc - same
plumbing, same hard-won reasons (Cloud Run has no durable local/in-memory
state across instances or deploys) as every other piece of persisted
state in this app.

REAL, DELIBERATE DIFFERENCE FROM BACKEND'S VERSION: this app shares its
Firestore project with andSons' backend/ service (see session_store.py's
_DEFAULT_PROJECT) - the collection name here is "ova_hero_usage", not the
unprefixed "hero_usage" backend uses, so the two apps' recency tracking
never reads or writes each other's documents (both apps happen to have a
same-named "weight_loss" category, which would otherwise silently mix
andSons' and OVA's hero picks into one shared, meaningless list)."""
import logging
from typing import List

from session_store import get_doc as _get_doc, set_doc as _set_doc

logger = logging.getLogger("hero_usage_tracker")

_COLLECTION = "ova_hero_usage"
_MAX_STORED = 60
_RECENCY_WINDOW = 12


def _doc_id(category: str) -> str:
    return category or "unknown"


def get_recent_heroes(category: str, limit: int = _RECENCY_WINDOW) -> List[str]:
    """The last `limit` hero keys actually picked for this category, most
    recent LAST. Fails soft (empty list) on any Firestore error - a
    missing recency signal should degrade to the old "no memory" behaviour,
    never block or crash a real generation."""
    try:
        doc = _get_doc(_COLLECTION, _doc_id(category))
        recent = (doc or {}).get("recent", [])
        return recent[-limit:]
    except Exception:
        logger.exception("Failed to read recent hero usage for category %r - proceeding with no recency signal.", category)
        return []


def record_hero_use(category: str, hero_key: str) -> None:
    """Appends one real, final hero decision to this category's history.
    Skips 'none' and empty/null values - a deliberate text-first choice is
    not a hero to avoid repeating."""
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

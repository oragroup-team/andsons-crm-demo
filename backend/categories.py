"""andSons CRM categories - Hair Loss, Sexual Health (ED/PE), Weight Loss,
Skin.

Source: the manager/marketing lead's own real reference doc
("andsons-crm-prompts-all-categories.md", shared 2026-08-25) describing the
real customer psychology, objections, and compliance sensitivity per
category - this file ports that content faithfully into the actual running
system (it previously only existed as reference prose, not connected to any
code). Real gap this fixes: flows.py and copywriter_agent.py had NO concept
of category at all before this - every flow was implicitly Hair Loss, so a
request for a Weight Loss or ED email had nothing category-specific to draw
on and came back generic (the real complaint that produced this file).

Each category's `otc_verified` flag matters: Hair Loss is the only category
with a real, verified OTC product catalogue in this system (Redensyl serum,
Trio, Kit - see copywriter_agent.py's _build_flow_brief). The others have
NO verified product/price data anywhere in this codebase - naming a
plausible-sounding OTC product or price for them would be exactly the kind
of invention this whole system's "never invent" rule exists to prevent, so
copywriter_agent.py must default those categories to consult/Rx framing
regardless of what a given flow's `allow_price` flag says, until real
product data is actually supplied.
"""

CATEGORIES = {
    "hair_loss": {
        "label": "Hair Loss",
        "otc_verified": True,
        "voice_notes": (
            "Emotionally charged - empathy, never shame. Destigmatise; make acting feel normal, private, "
            "easy. It's common and treatable. Results take roughly 3-4 months - the hardest moment is "
            "months 1-3, the 'I don't see anything yet' valley, where people quit. Copy for existing "
            "customers should set expectations and keep them motivated. Cosmetic, with a real prescription "
            "product ladder giving natural upsell paths."
        ),
        "objections": [
            "does it actually work",
            "side-effect fears",
            "cost",
            "discretion of delivery",
        ],
        "compliance_notes": (
            "Standard andSons compliance rules apply in full (see SYSTEM_PROMPT's COMPLIANCE section) - "
            "no elevated discretion requirement beyond the baseline."
        ),
        "discretion_level": "standard",
    },
    "sexual_health": {
        "label": "Sexual Health (ED / PE)",
        "otc_verified": False,
        "voice_notes": (
            "The highest-stigma, highest-urgency category (covers both ED and PE - often overlapping, "
            "frequently co-occurring). The reader may never have said this out loud to anyone. Your job "
            "is to make reaching out feel normal, private, and easy - not to sell hard. Write to the "
            "SITUATION and the relief of having a path, never to a named medicine or mechanism. PE "
            "specifically is often assumed untreatable and under-discussed - normalising is most of the "
            "work: 'this is common, this is treatable, here's the private way to start.' Discretion is a "
            "product feature, not an afterthought."
        ),
        "objections": [
            "embarrassment",
            "is this even treatable",
            "discretion of packaging and billing",
            "do I have to see someone in person",
            "cost",
        ],
        "compliance_notes": (
            "DISCRETION BY DEFAULT: assume the subject line, preheader, and any hero image will be seen "
            "by someone other than the customer (a lock screen, a shared inbox). No innuendo, no "
            "suggestive imagery or phrasing. Prescription/consultation-led - the tightest marketing "
            "constraints of any category; never name a specific medicine or mechanism, ever."
        ),
        "discretion_level": "high",
    },
    "weight_loss": {
        "label": "Weight Loss",
        "otc_verified": False,
        "voice_notes": (
            "Medically supervised and the most claim-sensitive category. Long treatment horizon with "
            "meaningful side-effect and expectation management; adherence and dose-escalation support "
            "drive retention. Write to health and capability, not appearance or aesthetics."
        ),
        "objections": [
            "does it work for me",
            "side effects",
            "cost and commitment",
            "have I failed at this before",
            "is it safe",
        ],
        "compliance_notes": (
            "NEVER make outcome promises - no guaranteed results, timelines, or weight figures. NEVER use "
            "before/after framing, body comparison, or aspirational-body imagery. NEVER body-shame, "
            "however gentle it seems - no reference to the reader's weight, size, or appearance as a "
            "problem. No BMI-based framing or self-assessment language."
        ),
        "discretion_level": "high",
    },
    "skin": {
        "label": "Skin",
        "otc_verified": False,
        "voice_notes": (
            "Largely cosmetic/OTC with some prescription pathways. Lowest compliance risk of the four "
            "categories, most routine-and-habit driven; retention comes from routine adherence and "
            "replenishment timing. Copy should make the routine feel simple and worth keeping."
        ),
        "objections": [
            "does it actually work",
            "will it suit my skin",
            "cost of keeping up the routine",
        ],
        "compliance_notes": (
            "Standard andSons compliance rules apply in full - lowest-sensitivity category, but the same "
            "no-outcome-promise and invent-nothing rules still hold (no fabricated 'clearer skin in X "
            "days' claims)."
        ),
        "discretion_level": "standard",
    },
}

VALID_CATEGORY_SLUGS = list(CATEGORIES.keys())
DEFAULT_CATEGORY = "hair_loss"


def category_notes_text(category: str) -> str:
    """Real per-category context block injected into the Copywriter/Sweeper
    prompts - see copywriter_agent.py's {category_notes} placeholder.
    Falls back to Hair Loss (the original, only-ever-supported category)
    for an unrecognised slug, matching image_bank.hero_bank_for_category()'s
    same fallback reasoning - never silently produce a category-less
    prompt."""
    cat = CATEGORIES.get(category, CATEGORIES[DEFAULT_CATEGORY])
    objections = ", ".join(cat["objections"])
    return (
        f"CATEGORY FOR THIS EMAIL: {cat['label']}.\n"
        f"{cat['voice_notes']}\n"
        f"Real objections this reader is likely weighing: {objections}.\n"
        f"CATEGORY-SPECIFIC COMPLIANCE: {cat['compliance_notes']}"
    )

"""Canonical andSons approved hero image bank - now covering every real
category (Hair Loss, Sexual Health, Weight Loss, Skin), not just Hair Loss.

Source: the real andSons Image_Bank asset folder (project root Image_Bank/,
including the real "hero_repost_p1-treatment-plan-created-not-pu_*" exports
used in actual sent P1 emails, and the ANDSONS HL PRODUCT SHOT subfolder) for
the original "hair_loss" entries, PLUS a real second batch of the manager's
own brand asset drive (2026-08-25, two partial "drive-download-..." folders
covering ~4.2GB of the real andSons "Email Template Image"/"Stock Photos"/
"Product Assets" library - too large to mirror in full, so only a genuinely
reviewed subset was pulled in). Every entry below was reviewed directly by
looking at the actual image, not invented or guessed from a filename - and
the source files were resized/re-encoded into backend/static/hero_images/,
served by Flask at /hero-images/<key>.jpg (see app.py) so they work both
locally and once deployed, without depending on any external host.

A REAL, HONEST GAP, not a placeholder to quietly fill in later: the newer
categories have far fewer reviewed images than Hair Loss (17 real entries)
- Sexual Health has 2, Weight Loss has 3, Skin has 3, plus 2 cross-category
entries (a real named andSons doctor, a general "resuming a routine" shot).
That's the true state of what's been reviewed and approved so far, not an
oversight - many of the raw "Stock Photos" folders for these categories
contain genuinely unsuitable images (explicit/suggestive shots for Sexual
Health that break the discretion rule, body-shaming "before" photos for
Weight Loss that break the never-shame rule) that were deliberately
excluded rather than included just to pad out the count. If a category
genuinely has no fitting photo for a given email, "none" (text-first) is
the correct, expected choice - it is not a smaller or lesser option than a
forced, mismatched photo.

Two entries ("smiling", "adjusting") are the "locked" heroes with a baked-in
headline (the real approved P1 golden example) - the Copywriter must NOT add
a separate overlay headline for these, since the image already carries one.
The rest are RAW photos with no baked text, so they need a concrete,
plain-language overlay headline (2-5 words) when used.

Every entry has a real `category` tag: one of "hair_loss", "sexual_health",
"weight_loss", "skin", or "general" (usable for ANY category - currently
just the real named doctor photo and the routine/resuming-a-habit shot).
Use hero_bank_for_category() to get the right subset for a given category's
email, rather than reading HERO_BANK directly and risking a Hair-Loss-only
photo (a man with his hand in his hair) surfacing on an ED/PE/Weight-Loss/
Skin email, where it would be actively wrong, not just a mismatch.

Selection is bank-only: there is no image-generation fallback. If none of
these genuinely fit an email, the correct choice is "none" (text-first).
"""

HERO_BANK = {
    "smiling": {
        "url": "/hero-images/smiling.jpg",
        "description": "Man smiling warmly, looking down and to the side, rust corduroy jacket over a white tee",
        "baked_headline": "Hair growth with real support",
        "moment": "warm, hopeful, affirming - e.g. 'your plan is ready'",
        "category": "hair_loss",
    },
    "adjusting": {
        "url": "/hero-images/adjusting.jpg",
        "description": "Man from behind, hand raised into his hair, rust/tan jacket, pale background",
        "baked_headline": "The earlier you begin, the more you keep",
        "moment": "reflective, early-action - the default P1 hero",
        "category": "hair_loss",
    },
    "earlysigns": {
        "url": "/hero-images/earlysigns.jpg",
        "description": "Straight back-of-head shot, close-cropped view of the crown, rust t-shirt, pale background",
        "baked_headline": None,
        "moment": "quiet concern about the crown/hairline, early-signs framing",
        "category": "hair_loss",
    },
    "combing": {
        "url": "/hero-images/combing.jpg",
        "description": "Mature man combing his hairline with a wooden comb, direct eye contact, salt-and-pepper stubble, white tee",
        "baked_headline": None,
        "moment": "grooming ritual, routine care, a slightly older reader",
        "category": "hair_loss",
    },
    "thoughtful": {
        "url": "/hero-images/thoughtful.jpg",
        "description": "Man with hand on his head, brow furrowed, looking up and away, rust jacket",
        "baked_headline": None,
        "moment": "quiet worry or uncertainty - noticing something, not yet resolved",
        "category": "hair_loss",
    },
    "redensyl": {
        "url": "/hero-images/redensyl.jpg",
        "description": "A hand holding the andSons 3% Redensyl Anti-Hair Loss Serum bottle, pale background",
        "baked_headline": None,
        "moment": "OTC product moments - cart abandon, upsell, Redensyl-first-choice",
        "category": "hair_loss",
    },
    "earlyaction": {
        "url": "/hero-images/earlyaction.jpg",
        "description": "Man from behind and slightly to the side, hand touching his hair, tan jacket, pale background",
        "baked_headline": None,
        "moment": "reflective, early-action, a quieter alternative to the 'adjusting' locked hero",
        "category": "hair_loss",
    },
    "leaning": {
        "url": "/hero-images/leaning.jpg",
        "description": "Man leaning against a wall, hand in his hair, faint content half-smile, rust t-shirt",
        "baked_headline": None,
        "moment": "at ease, settled - a decision already made or a good update landing",
        "category": "hair_loss",
    },
    "reflection": {
        "url": "/hero-images/reflection.jpg",
        "description": "Man with arms crossed, looking off to the side, pensive, rust jumper",
        "baked_headline": None,
        "moment": "considering something, weighing a decision, mid-thought",
        "category": "hair_loss",
    },
    "selfcare": {
        "url": "/hero-images/selfcare.jpg",
        "description": "Man with eyes closed, hand through damp hair, warm gradient background, white tee",
        "baked_headline": None,
        "moment": "calm self-care ritual - washing, treating, an unhurried moment",
        "category": "hair_loss",
    },
    "confidentsmile": {
        "url": "/hero-images/confidentsmile.jpg",
        "description": "Man with a big genuine smile, hand in his hair, rust jacket, pale background",
        "baked_headline": None,
        "moment": "joyful, confident - a milestone or results moment",
        "category": "hair_loss",
    },
    "editorial": {
        "url": "/hero-images/editorial.jpg",
        "description": "Moody profile portrait, finger resting near his chin, rust t-shirt, dark background",
        "baked_headline": None,
        "moment": "serious, editorial, a weightier moment - suits Rx-track reflection",
        "category": "hair_loss",
    },
    "warmsmile": {
        "url": "/hero-images/warmsmile.jpg",
        "description": "Close warm smile, hand in his hair, rust sweater, cream background - a real alternate P1 hero shot",
        "baked_headline": None,
        "moment": "warm, affirming, a softer alternative to the locked 'smiling' hero",
        "category": "hair_loss",
    },
    "resolved": {
        "url": "/hero-images/resolved.jpg",
        "description": "Serious, composed portrait against a dark background, rust t-shirt - a real alternate P1 hero shot",
        "baked_headline": None,
        "moment": "resolved, steady - having made the decision to start or continue treatment",
        "category": "hair_loss",
    },
    "decision": {
        "url": "/hero-images/decision.jpg",
        "description": "Close thoughtful portrait, hand resting near his chin, pale background - a real alternate P1 hero shot",
        "baked_headline": None,
        "moment": "weighing a decision, on the verge of committing",
        "category": "hair_loss",
    },
    "serumfloating": {
        "url": "/hero-images/serumfloating.jpg",
        "description": "The andSons 3% Redensyl serum bottle alone, floating product shot on a plain background",
        "baked_headline": None,
        "moment": "clean product-only moments - a single-item callout, restock reminders",
        "category": "hair_loss",
    },
    "productlineup": {
        "url": "/hero-images/productlineup.jpg",
        "description": "The andSons OTC range styled together (serum, biotin gummies, DHT blocker, shampoo, conditioner) on a warm terracotta background",
        "baked_headline": None,
        "moment": "cross-sell / AOV growth - showing the fuller range, not one product",
        "category": "hair_loss",
    },
    "kit": {
        "url": "/hero-images/kit.jpg",
        "description": "The Intense Hair Growth Kit (shampoo, conditioner, serum, dermaroller) staged on stone, warm background",
        "baked_headline": None,
        "moment": "premium kit / bundle moments - AOV growth, a considered upgrade",
        "category": "hair_loss",
    },
    # --- Sexual Health (ED, PE) - reviewed 2026-08-25. Only 2 real entries -
    # the discretion rule ruled out most of the raw stock photos available
    # (couples, overtly suggestive shots) as unsuitable, so this stays a
    # small, deliberately conservative set rather than padded with a
    # mismatched image.
    "quietmoment": {
        "url": "/hero-images/quietmoment.jpg",
        "description": "Man lying alone in bed, side profile, hand resting near his face, plain white bedding and wall, no one else in frame",
        "baked_headline": None,
        "moment": "private, reflective - a quiet moment alone, fits a discreet first-touch or reminder",
        "category": "sexual_health",
    },
    "weighingit": {
        "url": "/hero-images/weighingit.jpg",
        "description": "Man seated, hand on chin, thoughtful expression looking off to the side, plain pale studio background, blush pink t-shirt",
        "baked_headline": None,
        "moment": "weighing a decision, on the verge of reaching out - a discreet, non-suggestive portrait",
        "category": "sexual_health",
    },
    # --- Weight Loss - reviewed 2026-08-25. Deliberately excludes the
    # before/after, body-comparison, and "isolated on white pointing at his
    # belly" stock photos found in the same source folder - those directly
    # break this brand's own "no before/after, no body comparison, no
    # aspirational-body imagery" rule. These 3 focus on the person, not the
    # body, and read as confident/capable rather than a "problem" being
    # solved.
    "warmlook": {
        "url": "/hero-images/warmlook.jpg",
        "description": "Man glancing back over his shoulder with a warm smile, curly dark hair, plain white t-shirt, pale studio background",
        "baked_headline": None,
        "moment": "warm, approachable, confident - a good general opening moment, not body-focused",
        "category": "weight_loss",
    },
    "confidentease": {
        "url": "/hero-images/confidentease.jpg",
        "description": "A larger-bodied man smiling warmly and confidently, three-quarter turn, casual off-white t-shirt, plain pale background",
        "baked_headline": None,
        "moment": "confident, at ease, dignified - never a 'before' shot, a genuinely positive portrait",
        "category": "weight_loss",
    },
    "videoconsult": {
        "url": "/hero-images/videoconsult.jpg",
        "description": "Man waving at a laptop screen during an online video call, warm smile, seated at a wooden desk with soft natural light",
        "baked_headline": None,
        "moment": "booking or attending the online doctor consult - warm, welcoming, practical",
        "category": "weight_loss",
    },
    # --- Skin - reviewed 2026-08-25. Real andSons product shots (Daily
    # Serum, Daily Moisturiser) rather than face/body photography, since no
    # reviewed lifestyle portrait was available yet for this category - an
    # honest gap, not a placeholder.
    "dailyserum": {
        "url": "/hero-images/dailyserum.jpg",
        "description": "The andSons Daily Serum bottle (real product, black pump bottle) styled with lab flasks and a halved grapefruit, clean white background",
        "baked_headline": None,
        "moment": "clean, ingredient-led product moment - a serum-first-choice or restock message",
        "category": "skin",
    },
    "moisturiserhold": {
        "url": "/hero-images/moisturiserhold.jpg",
        "description": "A hand holding the andSons Daily Moisturiser bottle (real product, black pump bottle), plain light blue background",
        "baked_headline": None,
        "moment": "clean product-only moment - a single-item callout, restock reminder",
        "category": "skin",
    },
    "routineapply": {
        "url": "/hero-images/routineapply.jpg",
        "description": "Close shot of two hands - one dispensing the andSons Daily Moisturiser into the open palm of the other - plain blue-grey background",
        "baked_headline": None,
        "moment": "the routine itself, a calm daily-habit moment rather than a product callout",
        "category": "skin",
    },
    # --- General - usable for ANY category's email, reviewed 2026-08-25.
    "freshstart": {
        "url": "/hero-images/freshstart.jpg",
        "description": "Man drinking a green juice/smoothie through a straw, calm expression, sage green long-sleeve top, plain white background",
        "baked_headline": None,
        "moment": "resuming a healthy routine - fits a winback or restart moment in any category",
        "category": "general",
    },
    "doctor": {
        "url": "/hero-images/doctor.jpg",
        "description": "A real named andSons doctor (Dr Ben Ng) in black scrubs, arms crossed, warm confident smile, plain background",
        "baked_headline": None,
        "moment": "the doctor-led/consult-booking moment, in any category - never pair with copy that implies the customer can message him directly",
        "category": "general",
        # Real, live-caught finding (2026-08-25): the Sweeper itself
        # correctly hard-failed this hero on a Sexual Health email -
        # "pairing a named clinician's face with a sexual health email
        # undercuts the discretion this category requires." A real,
        # identifiable face is a genuinely bigger discretion risk for the
        # highest-stigma category than for the others this photo is
        # otherwise fine for - excluded here at the source rather than
        # relying on the Sweeper to keep catching it candidate after
        # candidate (confirmed live: the Copywriter kept re-proposing it
        # across retries despite the correction).
        "exclude_categories": ["sexual_health"],
    },
}

# "none" is always valid alongside the bank keys above, for a deliberate
# text-first email. There is no image-generation fallback - selection is
# bank-only.
SPECIAL_HERO_VALUES = ["none"]
HERO_KEYS = list(HERO_BANK.keys()) + SPECIAL_HERO_VALUES

CATEGORIES = ["hair_loss", "sexual_health", "weight_loss", "skin"]


def hero_bank_for_category(category: str) -> dict:
    """Real, live-caught reason this exists: giving the Copywriter the FULL
    HERO_BANK regardless of category would put Hair-Loss-only photos (a man
    with his hand in his hair, a Redensyl serum bottle) in front of an
    ED/PE/Weight-Loss/Skin email - not just a mismatch, but actively wrong
    for that category. Returns every entry tagged for this category PLUS
    every "general" entry (usable anywhere) - never entries from a
    DIFFERENT specific category. Falls back to the full "hair_loss" set
    (the original, largest, most-reviewed bank) for an unrecognised
    category string, so a typo or a not-yet-supported category never
    silently returns an empty bank."""
    if category not in CATEGORIES:
        category = "hair_loss"
    return {
        key: entry
        for key, entry in HERO_BANK.items()
        if entry["category"] in (category, "general")
        and category not in entry.get("exclude_categories", [])
    }

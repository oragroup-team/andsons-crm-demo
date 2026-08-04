"""Canonical andSons approved hero image bank.

Source: the real andSons Image_Bank asset folder (project root Image_Bank/,
including the real "hero_repost_p1-treatment-plan-created-not-pu_*" exports
used in actual sent P1 emails, and the ANDSONS HL PRODUCT SHOT subfolder).
Every entry below was reviewed directly - not invented - and the source
files were resized/re-encoded into backend/static/hero_images/, served by
Flask at /hero-images/<key>.jpg (see app.py) so they work both locally and
once deployed, without depending on any external host.

Two entries ("smiling", "adjusting") are the "locked" heroes with a baked-in
headline (the real approved P1 golden example) - the Copywriter must NOT add
a separate overlay headline for these, since the image already carries one.
The rest are RAW photos with no baked text, so they need a concrete,
plain-language overlay headline (2-5 words) when used.

Selection is bank-only: there is no image-generation fallback. If none of
these genuinely fit an email, the correct choice is "none" (text-first).
"""

HERO_BANK = {
    "smiling": {
        "url": "/hero-images/smiling.jpg",
        "description": "Man smiling warmly, looking down and to the side, rust corduroy jacket over a white tee",
        "baked_headline": "Hair growth with real support",
        "moment": "warm, hopeful, affirming - e.g. 'your plan is ready'",
    },
    "adjusting": {
        "url": "/hero-images/adjusting.jpg",
        "description": "Man from behind, hand raised into his hair, rust/tan jacket, pale background",
        "baked_headline": "The earlier you begin, the more you keep",
        "moment": "reflective, early-action - the default P1 hero",
    },
    "earlysigns": {
        "url": "/hero-images/earlysigns.jpg",
        "description": "Straight back-of-head shot, close-cropped view of the crown, rust t-shirt, pale background",
        "baked_headline": None,
        "moment": "quiet concern about the crown/hairline, early-signs framing",
    },
    "combing": {
        "url": "/hero-images/combing.jpg",
        "description": "Mature man combing his hairline with a wooden comb, direct eye contact, salt-and-pepper stubble, white tee",
        "baked_headline": None,
        "moment": "grooming ritual, routine care, a slightly older reader",
    },
    "thoughtful": {
        "url": "/hero-images/thoughtful.jpg",
        "description": "Man with hand on his head, brow furrowed, looking up and away, rust jacket",
        "baked_headline": None,
        "moment": "quiet worry or uncertainty - noticing something, not yet resolved",
    },
    "redensyl": {
        "url": "/hero-images/redensyl.jpg",
        "description": "A hand holding the andSons 3% Redensyl Anti-Hair Loss Serum bottle, pale background",
        "baked_headline": None,
        "moment": "OTC product moments - cart abandon, upsell, Redensyl-first-choice",
    },
    "earlyaction": {
        "url": "/hero-images/earlyaction.jpg",
        "description": "Man from behind and slightly to the side, hand touching his hair, tan jacket, pale background",
        "baked_headline": None,
        "moment": "reflective, early-action, a quieter alternative to the 'adjusting' locked hero",
    },
    "leaning": {
        "url": "/hero-images/leaning.jpg",
        "description": "Man leaning against a wall, hand in his hair, faint content half-smile, rust t-shirt",
        "baked_headline": None,
        "moment": "at ease, settled - a decision already made or a good update landing",
    },
    "reflection": {
        "url": "/hero-images/reflection.jpg",
        "description": "Man with arms crossed, looking off to the side, pensive, rust jumper",
        "baked_headline": None,
        "moment": "considering something, weighing a decision, mid-thought",
    },
    "selfcare": {
        "url": "/hero-images/selfcare.jpg",
        "description": "Man with eyes closed, hand through damp hair, warm gradient background, white tee",
        "baked_headline": None,
        "moment": "calm self-care ritual - washing, treating, an unhurried moment",
    },
    "confidentsmile": {
        "url": "/hero-images/confidentsmile.jpg",
        "description": "Man with a big genuine smile, hand in his hair, rust jacket, pale background",
        "baked_headline": None,
        "moment": "joyful, confident - a milestone or results moment",
    },
    "editorial": {
        "url": "/hero-images/editorial.jpg",
        "description": "Moody profile portrait, finger resting near his chin, rust t-shirt, dark background",
        "baked_headline": None,
        "moment": "serious, editorial, a weightier moment - suits Rx-track reflection",
    },
    "warmsmile": {
        "url": "/hero-images/warmsmile.jpg",
        "description": "Close warm smile, hand in his hair, rust sweater, cream background - a real alternate P1 hero shot",
        "baked_headline": None,
        "moment": "warm, affirming, a softer alternative to the locked 'smiling' hero",
    },
    "resolved": {
        "url": "/hero-images/resolved.jpg",
        "description": "Serious, composed portrait against a dark background, rust t-shirt - a real alternate P1 hero shot",
        "baked_headline": None,
        "moment": "resolved, steady - having made the decision to start or continue treatment",
    },
    "decision": {
        "url": "/hero-images/decision.jpg",
        "description": "Close thoughtful portrait, hand resting near his chin, pale background - a real alternate P1 hero shot",
        "baked_headline": None,
        "moment": "weighing a decision, on the verge of committing",
    },
    "serumfloating": {
        "url": "/hero-images/serumfloating.jpg",
        "description": "The andSons 3% Redensyl serum bottle alone, floating product shot on a plain background",
        "baked_headline": None,
        "moment": "clean product-only moments - a single-item callout, restock reminders",
    },
    "productlineup": {
        "url": "/hero-images/productlineup.jpg",
        "description": "The andSons OTC range styled together (serum, biotin gummies, DHT blocker, shampoo, conditioner) on a warm terracotta background",
        "baked_headline": None,
        "moment": "cross-sell / AOV growth - showing the fuller range, not one product",
    },
    "kit": {
        "url": "/hero-images/kit.jpg",
        "description": "The Intense Hair Growth Kit (shampoo, conditioner, serum, dermaroller) staged on stone, warm background",
        "baked_headline": None,
        "moment": "premium kit / bundle moments - AOV growth, a considered upgrade",
    },
}

# "none" is always valid alongside the bank keys above, for a deliberate
# text-first email. There is no image-generation fallback - selection is
# bank-only.
SPECIAL_HERO_VALUES = ["none"]
HERO_KEYS = list(HERO_BANK.keys()) + SPECIAL_HERO_VALUES

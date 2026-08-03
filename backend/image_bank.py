"""Canonical andSons approved hero image bank.

Source of truth: the real andSons CRM knowledge base
(CRM_Email_Generation_Data/&SONS CRM Knowledge/05-Image-Bank.md) plus the
live hero-selection doctrine from the production agent prompts
(Agent Prompts VERBATIM, 8 Jul 2026 snapshot). Every URL below is copied
verbatim from that doc - never invented - in the mandatory CDN format
(https://lh3.googleusercontent.com/d/<FILE_ID>=w1200) that renders in email
clients; plain Drive share links do not.

Two entries ("smiling", "adjusting") are the "locked" heroes with a baked-in
headline (per the approved P1 golden example) - the Copywriter must NOT add
a separate overlay headline for these, since the image already carries one.
The rest are RAW photos with no baked text, so they need a concrete,
plain-language overlay headline (2-5 words) when used.
"""

HERO_BANK = {
    "smiling": {
        "url": "https://lh3.googleusercontent.com/d/1X33SlGWSfR6pXJf-FLw4gF0iifHkIFwS=w1200",
        "description": "Man smiling, brown terracotta-adjacent jacket",
        "baked_headline": "Hair growth with real support",
        "moment": "warm, hopeful, affirming - e.g. 'your plan is ready'",
    },
    "adjusting": {
        "url": "https://lh3.googleusercontent.com/d/1QSPedZ12DSLPsrhVGsK6HPIIHvexxzim=w1200",
        "description": "Man adjusting his hair",
        "baked_headline": "The earlier you begin, the more you keep",
        "moment": "reflective, early-action (the P1 default hero)",
    },
    "earlysigns": {
        "url": "https://lh3.googleusercontent.com/d/1IPdFZq_zYaoDaa1hRfQlTjTfQ-hmwsoI=w1200",
        "description": "Man's head, back view",
        "baked_headline": None,
        "moment": "quiet concern, early-signs framing",
    },
    "combing": {
        "url": "https://lh3.googleusercontent.com/d/1X0TOR5GIQoBWttcSbTEsTnf_BJUMzROs=w1200",
        "description": "Man combing his beard with care",
        "baked_headline": None,
        "moment": "grooming ritual, routine",
    },
    "thoughtful": {
        "url": "https://lh3.googleusercontent.com/d/1U1x-etqZu02j1UUhnAaGdOsZhKzzQjiV=w1200",
        "description": "Thoughtful style pose",
        "baked_headline": None,
        "moment": "contemplative, early-action",
    },
    "redensyl": {
        "url": "https://lh3.googleusercontent.com/d/1kg1BxBohfqUn8EAepUamMbWalNOEoQqP=w1200",
        "description": "Man's hand holding the andSons Redensyl hair serum",
        "baked_headline": None,
        "moment": "OTC product moments - cart abandon, upsell, Redensyl-first-choice",
    },
}

# "none" (deliberate text-first email) and "generate" (AI-generated hero,
# only when no bank photo genuinely fits) are always valid alongside the
# bank keys above.
SPECIAL_HERO_VALUES = ["none", "generate"]
HERO_KEYS = list(HERO_BANK.keys()) + SPECIAL_HERO_VALUES

# The approved Gen-AI image prompt template (04-Visual-Identity.md), used as
# the base for any "generate" hero - the Copywriter supplies only the
# specific pose/scene line for this email.
IMAGE_STANDARD_PROMPT = (
    "A portrait of an asian man (Chinese or Japanese or Korean) in his mid-30s, in a natural pose, "
    "with soft studio lighting and a neutral or pale background (pale grey #f6f5f4 or pale beige "
    "#eeeae1). Real skin with some wrinkles, pigmentation, stubble for a man in his mid-30s; calm, "
    "confident, relatable expression, quietly confident, subtly hopeful, forward-looking - never sad, "
    "worried, anxious, or downcast. Clothing minimal and modern, solid-color crewneck or button-up in "
    "terracotta tones, Uniqlo-style, no logos or patterns, no text, no watermark. Photo-realistic, "
    "editorial, aspirational but authentic - inspired by Glossier, Zara, or Hims campaigns."
)

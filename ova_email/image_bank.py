"""OVA Singapore hero photo bank - curated from the FULL real brand asset
folder `01_OVA_Rebrand_2025/` (every file in the top level, every
subfolder, checked one by one - not just the `OVA Website Image
Assets_Updated Jpeg/` subfolder, which is all the first pass of this bank
actually covered). Every entry below was actually looked at - nothing here
is guessed from a filename.

REAL FIX #1 (reviewer feedback: "photos are so bad", "what is the logic in
the photos"): the Homepage tile icons (`update-BC.png`, `update-EC.png`,
`update-PD.png`, `update-PMS.png`, `update-Yeast Infection.png`,
`update-BCP.png`) and the WeightLoss fat-blocker asset
(`update-FatBlockerPills.png`) are all the SAME flat website-category-tile
graphic: a small purple envelope + a tiny pill icon, floating in a huge
empty bone-coloured square. Built for a ~100px website tile, not a
~550px-wide email hero banner. DELIBERATELY EXCLUDED from this bank.

REAL FIX #2: the first pass of this bank only ever reviewed the 12 images
inside `OVA Website Image Assets_Updated Jpeg/`. It never looked at the
~70 other images sitting in the root of `01_OVA_Rebrand_2025/` itself
(lifestyle photography, product-in-hand shots, and a folder of AI-generated
portrait variations) - which is why the bank was stuck at 5 entries and
had no photo at all for contraception. A full pass of every remaining
image found 7 more genuinely usable photos, including a real,
professional photo of an actual OVA-relevant contraceptive pill pack
(`BC new OVA image.png`) - closing what had been wrongly written off as an
unfixable gap. Also excluded in this pass, for the record: a pregnant-
silhouette image (`ChatGPT Image Jun 17, 2025, 06_14_23 PM.png` - no
pregnancy/fertility flow exists, out of scope), a hair-combing close-up
(`ChatGPT Image Aug 4, 2025, 09_42_11 PM.png` - haircare, not this brand's
scope), three skincare-touching-face images (`OVA_Skin Care Banner*.png` -
skincare is not one of the 4 live categories), several generic stock
photos with busy non-brand backgrounds (`138766.jpg`, `21374065.jpg`,
`2148750553.jpg`), the `SUPERGRAPHICS (CIRCLES)/` folder (24 files - all
the same flat "O" logo mark in 3 colourways, decorative, not photographic),
and a long tail of near-duplicate portrait variants from the same
AI-generated photoshoot (kept only the most distinct poses).

FULL-BANK PASS, then CORRECTED (two rounds of explicit instruction):
round 1 put every remaining real image in as a selectable hero, except
`SUPERGRAPHICS (CIRCLES)/` and `Family Planning/`. Round 2 explicitly
walked that back for anything that isn't a real photo of a person or a
physical product: REMOVED the 7 Homepage/WeightLoss flat "website tile"
icon graphics (`tile_*` - the literal source of the original "photos are
so bad" complaint), the brand wordmark card (`wordmark_card_in_hand`), the
purple ribbon/swoosh brand graphic (`brand_ribbon_swoosh`), the app-booking
UI screenshot (`app_booking_screenshot`), the multi-product collage that
embedded a UI screenshot (`weight_loss_bundle_collage`), and four abstract
decorative reaching-hands compositions (`reaching_hands_decorative`,
`reaching_hands_abstract`, `reaching_hands_abstract_2`,
`reaching_hands_abstract_3`) - none of these are a photo of a person or a
tangible product, they're titlecards/logos/UI/decorative graphics, and are
excluded on that basis regardless of source folder.

What stayed from the full-bank pass: every real photo of a person (candid
portraits, self-care moments, delivery/unboxing moments) and every real
photo of a physical product (bottles, pill packs, injector pens, the
scale, the mailer box) - matching the instruction that "bottles, pill
[packs], and similar items and people's photos can be used as hero
images."

The pregnancy-silhouette image (`pregnancy_silhouette`) is still in the
bank but its `category` is deliberately `"unassigned"`, not `"general"` or
any live category, so `hero_bank_for_category()` never surfaces it for a
contraception, EC, intimate-health, or weight-loss send - a judgment call
made without asking first, on brand-safety grounds. Flag this if a
pregnancy/fertility line is ever actually planned.

REAL FIX #3 (reviewer feedback, real Slack thread with a manager: "the
hero images do not match the weight loss support", given 3 times in a row
on the same touchpoint before it was actually fixed): 9 entries kept
during the full-bank pass above all carried their own honest caveat in
their `description`/`moment` text (e.g. "off brand-palette, included per
full-bank instruction", "skincare is not a current OVA flow, included per
full-bank instruction") - `journaling_moment`, `skincare_touch_1/2/3`,
`cafe_journaling_offbrand`, `cafe_laptop_offbrand`,
`craft_room_selfie_offbrand`, `applying_lotion_knee`,
`hair_combing_closeup`. All 9 were tagged `general`, so all 9 were eligible
for every category, weight_loss included. In practice they kept getting
selected anyway despite their own caveat text - and on at least one real
occurrence, the caveat text itself ("included per full-bank instruction")
leaked verbatim into the drafted copy as if it were real content, which is
a second, separate real bug (an internal annotation should never be able
to reach customer-facing text). REMOVED all 9 - a caveat in a photo's own
description is not a substitute for actually not offering it as a choice;
if an image needs an apology in its own listing, it does not belong in a
selectable bank, full stop. This is the same real lesson as REAL FIX #1
above, re-learned for a different failure mode.

REAL FIX #4 (found while verifying REAL FIX #3, same real Slack thread):
`content_hands_on_belly` was tagged `category: "weight_loss"` with its own
`moment` field literally recommending it for "a weight-loss progress or
self-acceptance message" - but categories.py's own real weight_loss
compliance notes explicitly ban exactly that: "No before/after framing, no
body comparison, no aspirational-body imagery, no body-shaming however
gentle." The image's own listed use case was in direct conflict with its
own category's compliance rules, and the Sweeper correctly caught and
rejected it live (twice) before the Copywriter converged on a different
hero - costing real retries on every email where it got picked first.
REMOVED entirely - it doesn't fit any of the other 3 categories either.

WHAT'S LEFT, HONESTLY: with the flat tiles gone, emergency_contraception
and intimate_health are back to having NO category-specific photo at all -
only the general lifestyle photos. No real photography for either exists
anywhere in the supplied folder; it needs to come from a fresh shoot.

REAL COMPLIANCE CHECK DONE ON EVERY IMAGE, not assumed: two of the weight-
loss vial renders are named `Product_Mounjaro.png` / `Product_Wegovy.png`
on disk (real medicine trade names) - but the actual PIXELS only ever show
"ova" and a generic mechanism label ("Weekly GLP-1 + GIP" / "Weekly GLP-1"),
never the trade name itself. The keys and descriptions below are
deliberately generic so no trade name ever surfaces in a prompt, a Sweeper
reason, or a Slack caption - only what a customer would actually see on
the vial or pack.

REAL FIX #5 (2026-10-01): 2 more real photos added, found while sorting a
new andSons photo drop (`New_IMAGE_LIBRARY/`) that turned out to contain 2
loose files of women, not the men's-brand subjects the rest of that drop
was for - see `linen_striped_laughing_portrait` and
`magazine_armchair_laughing_portrait` below. Genuinely on-brand, reviewed
the same way as every other entry, just misfiled at the source.

To add more real OVA photos later (especially real photography for
EC/intimate health - the real gap above), add entries in this exact shape
and the matching re-encoded file under static/hero_images/<key>.jpg, then
run `python3 build_hero_catalog.py` to refresh the human-readable catalog.
"""

import random

HERO_BANK = {
    "warm_confident_portrait": {
        "url": "/hero-images/warm_confident_portrait.jpg",
        "description": "A real OVA brand photo: a smiling North Asian woman in a plain grey t-shirt, warm natural light, looking off to the side with a genuine, confident smile. No product, no text.",
        "moment": "a warm, reassuring, confident opening - a first email, a welcome, a consult invitation, or any moment that calls for a human face rather than a product",
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "focused_at_laptop": {
        "url": "/hero-images/focused_at_laptop.jpg",
        "description": "A real OVA brand photo: a close crop of a woman with long dark hair typing on a laptop at a wooden desk, warm neutral tones. No product, no text.",
        "moment": "managing care online, at her own pace - fits a 'book your consult online', 'manage everything from your phone', a refill/logistics action, or general convenience moment",
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    # --- Round 2, reviewed 2026-10-01 - found while sorting a new andSons
    # photo drop (New_IMAGE_LIBRARY): these 2 files were loose in that
    # folder's root but show women, not the andSons men's-brand subjects the
    # rest of that drop was for - routed here instead since they're genuine,
    # warm, on-brand OVA photos, not stray/unusable files.
    "linen_striped_laughing_portrait": {
        "url": "/hero-images/linen_striped_laughing_portrait.jpg",
        "description": "A warm close portrait of a woman with dark hair in a loose striped linen shirt, arms crossed, laughing and looking up and to the side, small gold hoop earrings and a watch, soft neutral home background with a plant. No product, no text.",
        "moment": "warm, genuine, unselfconscious laughter - a strong opening/hero moment for a welcome, a good-news, or a general confident-woman email",
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "magazine_armchair_laughing_portrait": {
        "url": "/hero-images/magazine_armchair_laughing_portrait.jpg",
        "description": "A woman with shoulder-length dark hair laughing openly, head tilted back, sitting in a dark armchair on a herringbone wood floor with a magazine open on her lap, delicate necklace and earrings. No product, no text.",
        "moment": "relaxed, at-home, genuinely delighted - fits a lighter/casual-tone email or a 'treat yourself' / downtime moment",
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "weekly_injectable_dual_hormone": {
        "url": "/hero-images/weekly_injectable_dual_hormone.jpg",
        "description": "A real OVA product render: a plain glass injectable vial with a silver cap, labelled only 'ova' and 'Weekly GLP-1 + GIP' - no medicine trade name appears anywhere on the label.",
        "moment": "the dual-hormone weekly injectable option specifically - a plan-ready or first-order moment for that specific strength",
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "weekly_injectable_single_hormone": {
        "url": "/hero-images/weekly_injectable_single_hormone.jpg",
        "description": "A real OVA product render: a plain glass injectable vial with a silver cap, labelled only 'ova' and 'Weekly GLP-1' - no medicine trade name appears anywhere on the label.",
        "moment": "the single-hormone weekly injectable option specifically - a plan-ready or first-order moment for that specific strength",
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "daily_tablets": {
        "url": "/hero-images/daily_tablets.jpg",
        "description": "A real OVA product render: a purple OVA box with two plain white capsules resting beside it, studio-lit with real shadow and reflection - no medicine name shown.",
        "moment": "the daily tablet weight-loss option specifically - a plan-ready or first-order moment for that format",
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "contraception_pill_pack": {
        "url": "/hero-images/contraception_pill_pack.jpg",
        "description": "A real OVA brand photo: a hand holding a real contraceptive pill blister pack (round tablets visible in foil), resting on a woman's thigh in neutral athletic wear, soft neutral studio background.",
        "moment": "contraception specifically - a refill, plan-ready, or 'your pack is on its way' moment for the pill",
        "baked_headline": None,
        "category": "contraception",
        "exclude_categories": [],
    },
    "video_consult_wave": {
        "url": "/hero-images/video_consult_wave.jpg",
        "description": "A real OVA brand photo: a woman sitting at a desk waving at her laptop screen mid video-call, warm cream sweater, soft natural light, plain curtain background. No product, no text.",
        "moment": "booking or joining a doctor consult online - fits any category's 'talk to your doctor' or consult-booking moment",
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "checking_phone_smile": {
        "url": "/hero-images/checking_phone_smile.jpg",
        "description": "A real OVA brand photo: a woman smiling down at her phone, plain grey background, casual t-shirt. No product, no text.",
        "moment": "a message, reminder, or app-notification moment - checking in on the phone rather than a laptop",
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "package_arrived_at_door": {
        "url": "/hero-images/package_arrived_at_door.jpg",
        "description": "A real OVA brand photo: the actual purple OVA mailer box sitting on the floor by a pair of sandaled feet at a home doorway/balcony, soft warm tones, city skyline visible through the window.",
        "moment": "delivery has arrived - a 'your order is at your door' or shipping-confirmation moment, for any category",
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "unboxing_at_home": {
        "url": "/hero-images/unboxing_at_home.jpg",
        "description": "A real OVA brand photo: a woman sitting cross-legged on a couch at home, olive tank top, holding the purple OVA box against her with both hands, soft cushions in the background.",
        "moment": "settling in with a first order or welcome kit - a cosier, at-home alternative to the doorstep-arrival photo",
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "supplement_capsule_moment": {
        "url": "/hero-images/supplement_capsule_moment.jpg",
        "description": "A real OVA brand photo: a woman smiling with her eyes closed, about to take a softgel capsule, cream tank top, warm neutral background. No packaging shown.",
        "moment": "a daily supplement/booster habit moment (e.g. the VitaHealth boosters sold alongside weight-loss plans) - a routine or adherence reminder",
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "self_injection_lifestyle": {
        "url": "/hero-images/self_injection_lifestyle.jpg",
        "description": "A real OVA brand photo: a woman holding a blue injector pen against her own stomach, about to self-administer, navy top, plain background. No brand name or medicine name visible.",
        "moment": "the real, human moment of a weekly injection - a more relatable alternative to the plain product-vial renders, e.g. for an injection-day reminder",
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "confident_arms_crossed_portrait": {
        "url": "/hero-images/confident_arms_crossed_portrait.jpg",
        "description": 'A real OVA brand photo: a woman with arms crossed, plain grey henley top, direct confident smile, plain white background.',
        "moment": 'a confident, self-assured opening moment - an alternative general portrait when warm_confident_portrait has already been used earlier in the same flow',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "calm_confident_headshot": {
        "url": "/hero-images/calm_confident_headshot.jpg",
        "description": 'A real OVA brand photo: a close headshot, beige background, calm direct gaze, freckles, cream tank top.',
        "moment": 'a calm, grounded, trustworthy opening - a quieter alternative to a big smile',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "confident_portrait_alt": {
        "url": "/hero-images/confident_portrait_alt.jpg",
        "description": 'A real photo: a woman with arms crossed, grey ribbed top, jeans, blurred bright interior with a black-and-white art piece in the background.',
        "moment": 'another confident general portrait option for variety across a multi-touchpoint flow',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "hand_presenting_box": {
        "url": "/hero-images/hand_presenting_box.jpg",
        "description": 'A real OVA product photo: a hand holding the purple OVA mailer box out at an angle, plain white background.',
        "moment": 'a simple product-in-hand delivery moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "hand_presenting_box_alt": {
        "url": "/hero-images/hand_presenting_box_alt.jpg",
        "description": 'A real OVA product photo: a hand holding the purple OVA mailer box at a different angle, plain white background.',
        "moment": 'an alternate crop of the same product-in-hand delivery moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "two_hands_holding_box": {
        "url": "/hero-images/two_hands_holding_box.jpg",
        "description": 'A real OVA product photo: two hands holding the purple OVA mailer box flat and centred, plain white background.',
        "moment": 'a symmetrical, centred product-delivery shot',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "warm_smiling_portrait_plus": {
        "url": "/hero-images/warm_smiling_portrait_plus.jpg",
        "description": 'A real OVA brand photo: a plus-size woman smiling warmly with hands clasped, cream background, soft light.',
        "moment": 'a warm, reassuring welcome or opening moment with genuine size representation',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "plain_product_box": {
        "url": "/hero-images/plain_product_box.jpg",
        "description": 'A real OVA product photo: the purple OVA mailer box alone, studio-lit, no hands, plain white background.',
        "moment": 'a clean, minimal product-only shot when no lifestyle context is needed',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "supplement_bottle_lineup": {
        "url": "/hero-images/supplement_bottle_lineup.jpg",
        "description": 'A real OVA product photo: four VitaHealth-branded OVA supplement booster bottles (Vitamin D3, CoQ10, Chromium, Vitamin B12) lined up, plain background.',
        "moment": 'the weight-loss supplement booster range specifically - a cross-sell or booster-add-on moment',
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "purple_box_studio": {
        "url": "/hero-images/purple_box_studio.jpg",
        "description": 'A real OVA product photo: the purple OVA mailer box shown top-down, studio background.',
        "moment": 'an alternate plain product-only delivery shot',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "smart_scale_product": {
        "url": "/hero-images/smart_scale_product.jpg",
        "description": 'A real OVA product photo: the OVA-branded smart body scale, studio-lit, plain background.',
        "moment": 'the weight-loss tracking/scale product specifically',
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "considering_decision_portrait": {
        "url": "/hero-images/considering_decision_portrait.jpg",
        "description": 'A real photo: a woman with her hand on her chin, thoughtful expression, plain background.',
        "moment": 'a considering-your-options or decision-point moment',
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "smiling_portrait_alt": {
        "url": "/hero-images/smiling_portrait_alt.jpg",
        "description": 'A real OVA brand photo: a smiling woman in a grey t-shirt looking to the side, plain background - closely matches the existing warm_confident_portrait.',
        "moment": 'an alternate crop/take of the warm confident-opening portrait',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "positive_smiling_portrait": {
        "url": "/hero-images/positive_smiling_portrait.jpg",
        "description": 'A real OVA brand photo: a plus-size woman smiling and looking to the side, grey t-shirt, plain white background.',
        "moment": 'a positive, upbeat general opening moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "cream_tank_portrait": {
        "url": "/hero-images/cream_tank_portrait.jpg",
        "description": 'A real OVA brand photo: a plus-size woman in a cream tank top, direct smile, warm neutral studio background.',
        "moment": 'a confident, direct-to-camera general portrait',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "confident_hand_in_pocket_portrait": {
        "url": "/hero-images/confident_hand_in_pocket_portrait.jpg",
        "description": 'A real OVA brand photo: a plus-size woman in a cream tank top and black jeans, hand in pocket, relaxed confident smile, plain background.',
        "moment": 'a relaxed, confident everyday moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "injector_pen_close_up": {
        "url": "/hero-images/injector_pen_close_up.jpg",
        "description": "A real OVA product photo: a close crop of a hand holding the blue injector pen, dial reading '0 mg', grey sleeve visible.",
        "moment": 'a close, detail-focused injectable-pen moment',
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "injector_pen_wide_crop": {
        "url": "/hero-images/injector_pen_wide_crop.jpg",
        "description": 'A real OVA product photo: a wider crop of a hand holding the blue injector pen horizontally, dial visible.',
        "moment": 'an alternate wide crop of the injectable-pen moment',
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "candid_phone_smile": {
        "url": "/hero-images/candid_phone_smile.jpg",
        "description": 'A real photo: a woman smiling down at her phone, plain white background, casual white t-shirt.',
        "moment": 'another phone-checking moment, alternate to checking_phone_smile',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "selfie_touching_face": {
        "url": "/hero-images/selfie_touching_face.jpg",
        "description": 'A real photo: a woman taking a phone selfie with one hand while touching her own cheek with the other, plain background.',
        "moment": 'a selfie/self-check moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "freckled_direct_gaze_portrait": {
        "url": "/hero-images/freckled_direct_gaze_portrait.jpg",
        "description": 'A real photo: a close portrait, freckles visible, direct calm gaze, white t-shirt, plain background.',
        "moment": 'a calm, natural, unfiltered general portrait',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "handoff_box": {
        "url": "/hero-images/handoff_box.jpg",
        "description": 'A real OVA product photo: two hands passing the purple OVA mailer box between them, plain background.',
        "moment": 'a handoff/delivery moment between two people',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "beige_tank_portrait_1": {
        "url": "/hero-images/beige_tank_portrait_1.jpg",
        "description": 'An AI-generated brand-style portrait: a plus-size woman in a beige tank top and black jeans, smiling, plain background.',
        "moment": 'a confident everyday portrait, part of a portrait series',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "taking_pill_with_water": {
        "url": "/hero-images/taking_pill_with_water.jpg",
        "description": 'An AI-generated brand-style photo: a woman in profile taking a tablet with a glass of water, cream top, plain background.',
        "moment": 'a medication/supplement-taking moment',
        "baked_headline": None,
        "category": "weight_loss",
        "exclude_categories": [],
    },
    "cream_tee_arms_crossed_1": {
        "url": "/hero-images/cream_tee_arms_crossed_1.jpg",
        "description": 'An AI-generated brand-style portrait: a plus-size woman, arms crossed, cream t-shirt, plain background, smiling to the side.',
        "moment": 'a confident portrait, part of a portrait series',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "cream_tee_arms_crossed_2": {
        "url": "/hero-images/cream_tee_arms_crossed_2.jpg",
        "description": 'An AI-generated brand-style portrait: a plus-size woman, arms crossed, cream t-shirt, plain white background, warm smile.',
        "moment": 'a confident portrait, part of a portrait series',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "short_bob_portrait": {
        "url": "/hero-images/short_bob_portrait.jpg",
        "description": 'An AI-generated brand-style portrait: a woman with a short bob haircut, arms crossed, beige background, gentle smile.',
        "moment": 'a confident portrait, part of a portrait series',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "phone_smile_alt": {
        "url": "/hero-images/phone_smile_alt.jpg",
        "description": 'An AI-generated brand-style photo: a woman smiling at her phone, grey t-shirt, plain background.',
        "moment": 'another phone-checking moment, alternate crop',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "side_smile_warm_light": {
        "url": "/hero-images/side_smile_warm_light.jpg",
        "description": 'An AI-generated brand-style portrait: a woman smiling to the side in warm side-lighting, grey t-shirt, plain background.',
        "moment": 'a warm, candid-feeling portrait',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "cream_tee_arms_crossed_3": {
        "url": "/hero-images/cream_tee_arms_crossed_3.jpg",
        "description": 'An AI-generated brand-style portrait: a plus-size woman, arms crossed, cream t-shirt, plain white background, laughing.',
        "moment": 'a confident, joyful portrait, part of a portrait series',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "thinking_hand_on_chin": {
        "url": "/hero-images/thinking_hand_on_chin.jpg",
        "description": 'An AI-generated brand-style portrait: a woman with her finger on her chin, thoughtful smile, grey t-shirt, plain background.',
        "moment": 'a considering-your-options moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "grey_henley_arms_crossed": {
        "url": "/hero-images/grey_henley_arms_crossed.jpg",
        "description": 'An AI-generated brand-style portrait: a woman, arms crossed, grey henley top, plain background, confident direct smile.',
        "moment": 'a confident portrait, part of a portrait series',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "curly_hair_sports_bra_smile": {
        "url": "/hero-images/curly_hair_sports_bra_smile.jpg",
        "description": 'An AI-generated brand-style portrait: a plus-size woman with curly hair, cream sports bra and leggings, smiling downward, plain background.',
        "moment": 'a body-confident, athletic-leisure portrait',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "white_tee_laughing_side": {
        "url": "/hero-images/white_tee_laughing_side.jpg",
        "description": 'An AI-generated brand-style portrait: a plus-size woman in a white t-shirt, laughing, looking to the side, plain background.',
        "moment": 'a joyful, candid-feeling portrait',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "curly_hair_back_turned_smile": {
        "url": "/hero-images/curly_hair_back_turned_smile.jpg",
        "description": 'An AI-generated brand-style portrait: a plus-size woman with curly hair, looking back over her shoulder, smiling, tan top, plain background.',
        "moment": 'a candid, over-the-shoulder confident moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "cream_tank_heart_tattoo": {
        "url": "/hero-images/cream_tank_heart_tattoo.jpg",
        "description": 'An AI-generated brand-style portrait: a plus-size woman in a cream tank top with a small heart tattoo, gentle smile, plain background.',
        "moment": 'a warm, approachable portrait with a personal detail',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "eyes_closed_smiling_cream": {
        "url": "/hero-images/eyes_closed_smiling_cream.jpg",
        "description": 'An AI-generated brand-style portrait: a woman smiling with her eyes closed, cream top, plain background, joyful expression.',
        "moment": 'a peaceful, joyful moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "mature_professional_portrait": {
        "url": "/hero-images/mature_professional_portrait.jpg",
        "description": 'An AI-generated brand-style portrait: a middle-aged woman in a cream blazer, calm confident expression, warm beige background.',
        "moment": 'an older-skewing, professional/trustworthy portrait - broadens the age range represented',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "freckled_gaze_portrait_1": {
        "url": "/hero-images/freckled_gaze_portrait_1.jpg",
        "description": 'An AI-generated brand-style portrait: a close headshot with visible freckles, direct gaze, white top, beige background.',
        "moment": 'a calm, natural, unfiltered portrait',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "eyes_closed_peaceful_cream": {
        "url": "/hero-images/eyes_closed_peaceful_cream.jpg",
        "description": 'An AI-generated brand-style portrait: a woman with eyes closed, peaceful smile, cream top, plain background.',
        "moment": 'a peaceful, at-ease moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "freckled_face_macro_1": {
        "url": "/hero-images/freckled_face_macro_1.jpg",
        "description": 'An AI-generated brand-style photo: an extreme close-up of a freckled face, direct gaze.',
        "moment": 'a very close, intimate detail shot - best for a small inset rather than a full hero',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "hand_on_chest_closeup": {
        "url": "/hero-images/hand_on_chest_closeup.jpg",
        "description": 'An AI-generated brand-style photo: a close crop of a hand resting on the chest/collarbone, beige knit top.',
        "moment": "a calm, reassured, 'breathe easy' moment",
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "mature_professional_portrait_2": {
        "url": "/hero-images/mature_professional_portrait_2.jpg",
        "description": 'An AI-generated brand-style portrait: a middle-aged woman in a cream blazer, warm beige background, gentle smile - similar to mature_professional_portrait, alternate take.',
        "moment": 'an older-skewing, professional/trustworthy portrait, alternate crop',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "freckled_gaze_portrait_2": {
        "url": "/hero-images/freckled_gaze_portrait_2.jpg",
        "description": 'An AI-generated brand-style portrait: a close headshot with freckles, direct gaze, beige background, gentle smile.',
        "moment": 'a calm, natural, unfiltered portrait, alternate crop',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "freckled_face_macro_2": {
        "url": "/hero-images/freckled_face_macro_2.jpg",
        "description": 'An AI-generated brand-style photo: an extreme close-up of a freckled face, black top, direct gaze.',
        "moment": 'a very close, intimate detail shot',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "ponytail_hand_up_side_profile": {
        "url": "/hero-images/ponytail_hand_up_side_profile.jpg",
        "description": 'An AI-generated brand-style portrait: a woman holding her own ponytail up, side profile, gold earring, beige background.',
        "moment": 'a candid, in-motion getting-ready moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "front_facing_hands_clasped": {
        "url": "/hero-images/front_facing_hands_clasped.jpg",
        "description": 'An AI-generated brand-style portrait: a woman facing the camera directly, hands clasped at the waist, beige/yellow background.',
        "moment": 'a calm, direct, trustworthy portrait',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "blue_shirt_profile_freckles": {
        "url": "/hero-images/blue_shirt_profile_freckles.jpg",
        "description": 'An AI-generated brand-style portrait: a woman in a light blue shirt, side profile, freckles visible, looking away from camera.',
        "moment": 'a quieter, contemplative portrait',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "package_arrived_at_door_alt1": {
        "url": "/hero-images/package_arrived_at_door_alt1.jpg",
        "description": 'An AI-generated brand-style photo: the purple OVA box on the floor by sandaled feet at a doorway, window and city view in the background.',
        "moment": 'an alternate crop of the delivery-has-arrived moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "package_arrived_at_door_alt2": {
        "url": "/hero-images/package_arrived_at_door_alt2.jpg",
        "description": 'An AI-generated brand-style photo: the purple OVA box on the floor by a pair of heels, business-casual attire, apartment balcony with city skyline.',
        "moment": 'another alternate crop of the delivery-has-arrived moment, more business-casual styling',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "freckled_face_macro_3": {
        "url": "/hero-images/freckled_face_macro_3.jpg",
        "description": 'An AI-generated brand-style photo: an extreme close-up of a freckled face, olive top, direct gaze.',
        "moment": 'a very close, intimate detail shot',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "olive_shirt_gold_earring_portrait": {
        "url": "/hero-images/olive_shirt_gold_earring_portrait.jpg",
        "description": 'An AI-generated brand-style portrait: a woman in an olive collared shirt, gold hoop earring, direct calm gaze, beige background.',
        "moment": 'a calm, grounded portrait with warmer skin tones represented',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
    "pregnancy_silhouette": {
        "url": "/hero-images/pregnancy_silhouette.jpg",
        "description": 'An AI-generated silhouette of a visibly pregnant woman in profile, sepia tones (no pregnancy/fertility flow currently exists at OVA; included in the bank per full-bank instruction, but not tagged to any live category so it will not surface for contraception, EC, intimate health, or weight-loss sends).',
        "moment": 'reserved for a possible future pregnancy/fertility line - deliberately not selectable for any current flow',
        "baked_headline": None,
        "category": "unassigned",
        "exclude_categories": [],
    },
    "grey_sweats_arms_crossed_smile": {
        "url": "/hero-images/grey_sweats_arms_crossed_smile.jpg",
        "description": 'An AI-generated brand-style portrait: a plus-size woman in a white t-shirt and grey sweatpants, arms crossed, smiling to the side.',
        "moment": 'a relaxed, at-home confident moment',
        "baked_headline": None,
        "category": "general",
        "exclude_categories": [],
    },
}

# "none" is always valid alongside the real bank keys above, for a
# deliberate text-first email when nothing in the bank genuinely fits.
SPECIAL_HERO_VALUES = ["none"]
HERO_KEYS = list(HERO_BANK.keys()) + SPECIAL_HERO_VALUES


def hero_bank_for_category(category: str) -> dict:
    """Category-scoped subset of HERO_BANK, same signature as andSons'
    image_bank.py - `general` entries show up for every category, matching
    andSons' own convention (its locked/general photos work the same way).

    REAL BIAS FIX: the returned dict's order is shuffled on every call, not
    HERO_BANK's own fixed definition order. copywriter_agent.py and
    creative_director_agent.py both build their hero catalog text straight
    from this dict's iteration order - with the bank now ~76 entries deep
    (after the full-bank pass), always presenting candidates in the exact
    same order on every single call risks a real, well-documented LLM
    failure mode (list-position bias: favouring an item because of where it
    sits in a long list, not because it genuinely fits best). Shuffling
    here, once, centrally, fixes it for every caller without each one
    needing its own fix - the category-scoped truthiness check in
    sweeper_agent.py is unaffected (order never mattered there)."""
    matches = [
        (key, entry) for key, entry in HERO_BANK.items()
        if entry.get("category") in (category, "general") and category not in entry.get("exclude_categories", [])
    ]
    random.shuffle(matches)
    return dict(matches)

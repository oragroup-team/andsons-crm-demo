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

REAL FIX #2 (2026-10-01): a stakeholder (Thalia) flagged the Weight Loss
bank specifically as thin/weak. Reviewed the manager's new
"New_IMAGE_LIBRARY/ASWL_SEPT26" drop (ova_email/New_IMAGE_LIBRARY/ - shared
across both app folders since it isn't OVA-specific) image by image: 8
genuine, warm, dignified photos of plus-size men in real everyday moments
(walking, laughing, phone, thinking, coffee) were added to weight_loss,
plus 2 more general-purpose mature-lifestyle shots from the same drop's
root folder. Several near-identical crops of the same underlying photo
shoot (extra square crops of the same laughing-at-phone moment, a solo
crop pulled from the two-men-walking photo, a transparent-background
cutout of the grass-field photo) were deliberately left out as redundant,
not reviewed-and-rejected for quality - see the "round 2" comments below
for exactly which crop was kept and why. Two more files in that same drop
showed women, not men - those don't belong in andSons' bank at all (this
is a men's brand) and were added to OVA's bank instead (see
ova_email/image_bank.py's own "round 2" entries).

A REAL, HONEST GAP, not a placeholder to quietly fill in later: Sexual
Health and Skin are still much thinner than Hair Loss (17 entries) or the
now-11-strong Weight Loss - Sexual Health has 2, Skin has 3, plus 4
cross-category "general" entries. That's the true state of what's been
reviewed and approved so far, not an oversight - many of the raw source
folders for these categories contain genuinely unsuitable images
(explicit/suggestive shots for Sexual Health that break the discretion
rule, body-shaming "before" photos for Weight Loss that break the
never-shame rule) that were deliberately excluded rather than included
just to pad out the count. If a category genuinely has no fitting photo
for a given email, "none" (text-first) is the correct, expected choice -
it is not a smaller or lesser option than a forced, mismatched photo.

Two entries ("smiling", "adjusting") are the "locked" heroes with a baked-in
headline (the real approved P1 golden example) - the Copywriter must NOT add
a separate overlay headline for these, since the image already carries one.
The rest are RAW photos with no baked text, so they need a concrete,
plain-language overlay headline (2-5 words) when used.

Every entry has a real `category` tag: one of "hair_loss", "sexual_health",
"weight_loss", "skin", or "general" (usable for ANY category). Use
hero_bank_for_category() to get the right subset for a given category's
email, rather than reading HERO_BANK directly and risking a Hair-Loss-only
photo (a man with his hand in his hair) surfacing on an ED/PE/Weight-Loss/
Skin email, where it would be actively wrong, not just a mismatch.

A REAL GAP ACROSS THE WHOLE BANK, not yet closed: the project's own
Image_Bank/ source folder (project root, gitignored) has ~70 real files in
total, of which only a subset is individually catalogued here - the rest
(mostly generically-named "Image Generation"/"ChatGPT Image [date]" files,
plus most of the real ANDSONS HL PRODUCT SHOT product renders) have not yet
been individually reviewed for inclusion. Treat the current bank as
comprehensive for Weight Loss specifically (the category that was
flagged), not yet as a complete catalogue of every usable asset on disk.

Selection is bank-only: there is no image-generation fallback. If none of
these genuinely fit an email, the correct choice is "none" (text-first).
"""

import random

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
    # --- Hair Loss, round 2 - reviewed 2026-10-01, from a full pass of the
    # remaining real Image_Bank/ files (both the main folder's generic
    # "ChatGPT Image"/"Image Generation" files and the ANDSONS HL PRODUCT
    # SHOT subfolder's product renders) that the first pass never got to.
    # Real, deliberate exclusions from this same pass, for the record: two
    # near-identical mint-green product shots with a stray AI-tool sparkle
    # watermark baked into a corner (kept only the one where the watermark
    # could be cropped out cleanly), two product renders with garbled/
    # misspelled label text ("andSans", "Redonayl" - an AI text-rendering
    # defect, not a real label), one shower photo that turned out to be
    # unfixably mirrored (the brand text was garbled, not just flipped),
    # a two-handed "gripping hair in distress" stock photo that read as
    # over-dramatized/stigmatizing rather than this brand's dignified tone,
    # several near-duplicate crops of the same shoot (e.g. three versions of
    # the same "pink sweater, phone, laughing" moment), and several more
    # generic pensive/confident portraits that didn't add a genuinely new
    # moment beyond what round 1 already covered.
    "profiledown": {
        "url": "/hero-images/profiledown.jpg",
        "description": "Man seen from behind and slightly to the side, looking down, rust t-shirt, pale background",
        "baked_headline": None,
        "moment": "quiet, uncertain, looking inward - a softer alternative to 'thoughtful'",
        "category": "hair_loss",
    },
    "hairlinetouch": {
        "url": "/hero-images/hairlinetouch.jpg",
        "description": "Close front-facing portrait, hand at the hairline, calm composed direct gaze, rust sweater, pale background",
        "baked_headline": None,
        "moment": "neither worried nor celebrating - a calm, neutral-toned moment for an informational or routine message",
        "category": "hair_loss",
    },
    "armscrossedfront": {
        "url": "/hero-images/armscrossedfront.jpg",
        "description": "Front-facing portrait, arms crossed, confident relaxed half-smile, brown t-shirt, pale background",
        "baked_headline": None,
        "moment": "confident and direct, not pensive - a good steady/reassuring opening moment",
        "category": "hair_loss",
    },
    "combinghairline": {
        "url": "/hero-images/combinghairline.jpg",
        "description": "Man combing his hairline with a black comb, other hand steadying his head, warm brown background",
        "baked_headline": None,
        "moment": "a grooming ritual using a real tool, not just a hand - routine care framing",
        "category": "hair_loss",
    },
    "showerwash": {
        "url": "/hero-images/showerwash.jpg",
        "description": "Close shot of a hand massaging shampoo lather into the hair and scalp, real suds visible, pale background",
        "baked_headline": None,
        "moment": "the actual washing routine - a shampoo or scalp-care moment",
        "category": "hair_loss",
    },
    "toweldry": {
        "url": "/hero-images/toweldry.jpg",
        "description": "Man drying his hair with a white towel, profile view, calm expression, rust t-shirt, warm cream background",
        "baked_headline": None,
        "moment": "the post-shower routine - a calm, practical everyday moment",
        "category": "hair_loss",
    },
    "serumsmile": {
        "url": "/hero-images/serumsmile.jpg",
        "description": "Man smiling and holding the real andSons 3% Redensyl Anti-Hair Loss Serum bottle up near his face, plain white background",
        "baked_headline": None,
        "moment": "a happy customer together with the real product - a different, warmer alternative to a hand-only product shot",
        "category": "hair_loss",
    },
    "lookingup": {
        "url": "/hero-images/lookingup.jpg",
        "description": "Profile portrait, chin up, looking upward, long hair flowing back, small earring visible, plain background",
        "baked_headline": None,
        "moment": "aspirational, optimistic, looking forward - a distinct upward-looking alternative to the mostly downward-gazing set",
        "category": "hair_loss",
    },
    "earlysignsparted": {
        "url": "/hero-images/earlysignsparted.jpg",
        "description": "Profile view, a hand parting the hair at the crown to clearly show early thinning, rust t-shirt, pale background",
        "baked_headline": None,
        "moment": "a direct, literal 'noticing early signs' moment - calm and exploratory, not alarmed",
        "category": "hair_loss",
    },
    "earlysignsclose": {
        "url": "/hero-images/earlysignsclose.jpg",
        "description": "Close overhead-angle shot, a hand parting the hair to reveal a thinning patch at the crown, rust t-shirt",
        "baked_headline": None,
        "moment": "the clearest, most literal early-signs visual in the bank - pairs well with first-contact or diagnosis-stage copy",
        "category": "hair_loss",
    },
    "scalpmassager": {
        "url": "/hero-images/scalpmassager.jpg",
        "description": "Man from behind using a black silicone scalp massager on wet hair, real bathroom setting with sink and mirror visible",
        "baked_headline": None,
        "moment": "a real device-based routine moment, not just a hand or a bottle - fits a scalp-care or accessory callout",
        "category": "hair_loss",
    },
    "sprayapply": {
        "url": "/hero-images/sprayapply.jpg",
        "description": "Man smiling while spraying the real andSons serum bottle (legible label) directly onto his hair, bright plain background",
        "baked_headline": None,
        "moment": "the product actually being used, not just held - good for a 'how to apply' or routine-reminder moment",
        "category": "hair_loss",
    },
    "overshoulder": {
        "url": "/hero-images/overshoulder.jpg",
        "description": "Man glancing back over his shoulder, intense confident gaze, dark maroon t-shirt, warm moody background",
        "baked_headline": None,
        "moment": "a more dramatic, editorial confident moment - distinct body angle from the mostly front/profile set",
        "category": "hair_loss",
    },
    "serumretail": {
        "url": "/hero-images/serumretail.jpg",
        "description": "Real product photography: two andSons 3% Redensyl Anti-Hair Loss Serum bottles and the retail box, warm tan background, wooden ball prop",
        "baked_headline": None,
        "moment": "clean retail-style product moment - real photography, not a render",
        "category": "hair_loss",
    },
    "fullrangeshelf": {
        "url": "/hero-images/fullrangeshelf.jpg",
        "description": "Real product photography: the full andSons range (serum, shampoo, conditioner, Daily Cleanser, Daily Moisturiser) on a warm orange-gradient shelf",
        "baked_headline": None,
        "moment": "cross-sell across the whole range, hair and skin together - real photography",
        "category": "hair_loss",
    },
    "cleanshelf": {
        "url": "/hero-images/cleanshelf.jpg",
        "description": "Real product photography: shampoo, conditioner, and serum on a glass shelf with soft shadows, minimal white background",
        "baked_headline": None,
        "moment": "a clean, minimal alternate product moment - real photography, different styling from the warm-toned shots",
        "category": "hair_loss",
    },
    "moisturiserlineup": {
        "url": "/hero-images/moisturiserlineup.jpg",
        "description": "Real product photography, wide landscape crop: shampoo, conditioner, and the Daily Moisturiser on a green-gradient background with a cream swatch",
        "baked_headline": None,
        "moment": "a wide banner-ready shot spanning hair and skin care together",
        "category": "hair_loss",
    },
    "otcrangeplus": {
        "url": "/hero-images/otcrangeplus.jpg",
        "description": "The andSons OTC range including Biotin Gummies, DHT Blocker, shampoo, and conditioner, staged on an orange-to-tan gradient",
        "baked_headline": None,
        "moment": "a broader cross-sell shot than 'productlineup' - includes the gummies and DHT blocker",
        "category": "hair_loss",
    },
    "bannertrio": {
        "url": "/hero-images/bannertrio.jpg",
        "description": "Serum, conditioner, and the dermaroller staged together on an orange gradient, wide crop with generous open space for a headline",
        "baked_headline": None,
        "moment": "a banner-ready product trio shot with built-in copy space",
        "category": "hair_loss",
    },
    "serumfoam": {
        "url": "/hero-images/serumfoam.jpg",
        "description": "The andSons serum bottle on real shampoo foam/suds, mint-green background",
        "baked_headline": None,
        "moment": "a fresher, foam-styled alternate to the plain-background serum shots",
        "category": "hair_loss",
    },
    "shampoofoam": {
        "url": "/hero-images/shampoofoam.jpg",
        "description": "The andSons shampoo bottle on real foam/suds, mint-green background",
        "baked_headline": None,
        "moment": "a shampoo-specific product moment - distinct from the serum-only shots",
        "category": "hair_loss",
    },
    "fullrangereflective": {
        "url": "/hero-images/fullrangereflective.jpg",
        "description": "The full andSons range (serum, conditioner, shampoo, Daily Cleanser, Daily Moisturiser) on a reflective bronze surface, orange-gradient background",
        "baked_headline": None,
        "moment": "the widest cross-category range shot in the bank - hair and skin together",
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
    # --- Weight Loss, round 2 - reviewed 2026-10-01, from the manager's
    # "New_IMAGE_LIBRARY/ASWL_SEPT26" drop (andSons Weight Loss, Sept 2026).
    # This is the real fix for the thin-weight_loss-coverage complaint: 8
    # genuine, warm, dignified photos of plus-size men in real everyday
    # moments (walking, laughing, on the phone, thinking, having coffee) -
    # never a "before" shot, never body comparison, never isolated-on-white
    # pointing at a belly. Several near-identical crops of the same shoot
    # (e.g. two other square crops of the "phonelaugh" moment, a solo crop
    # pulled from the "walktogether" pair, a transparent-background cutout
    # of "outdoorlaugh") were deliberately left out as redundant, not
    # reviewed-and-rejected for quality.
    "outdoorglance": {
        "url": "/hero-images/outdoorglance.jpg",
        "description": "A plus-size man in a green t-shirt and khaki trousers, glancing back over his shoulder with a big genuine smile while walking, clear blue sky background, wide candid crop with open space to one side",
        "baked_headline": None,
        "moment": "confident, mid-stride, caught off guard by something good - a strong opening/hero moment, not body-focused",
        "category": "weight_loss",
    },
    "phonelaugh": {
        "url": "/hero-images/phonelaugh.jpg",
        "description": "A plus-size man in a coral sweater, sitting with legs crossed, laughing at his phone, plain blue-grey background, wide crop with generous open space to the left for a headline",
        "baked_headline": None,
        "moment": "a light, everyday phone moment - good for a casual nudge or reminder email",
        "category": "weight_loss",
    },
    "quietreflection": {
        "url": "/hero-images/quietreflection.jpg",
        "description": "A plus-size man in a beige sweater, sitting with hand on chin in a thoughtful pose, looking up and to the side, plain grey-blue background",
        "baked_headline": None,
        "moment": "quietly thinking something over - fits a 'considering your options' or decision-point email, not a sad or defeated moment",
        "category": "weight_loss",
    },
    "walktogether": {
        "url": "/hero-images/walktogether.jpg",
        "description": "Two plus-size men walking together through a sunlit grassy field, both laughing, one holding a water bottle, candid side-on view",
        "baked_headline": None,
        "moment": "doing this with someone, not alone - good for a community/support or 'you're not the only one' angle",
        "category": "weight_loss",
    },
    "loungeconfidence": {
        "url": "/hero-images/loungeconfidence.jpg",
        "description": "A plus-size man in a black long-sleeve top, relaxed in an armchair in a moody, book-lined lounge, warm confident half-smile, wedding ring visible on his hand",
        "baked_headline": None,
        "moment": "settled, confident, at home in his own life - a good calm/established-routine moment",
        "category": "weight_loss",
    },
    "outdoorlaugh": {
        "url": "/hero-images/outdoorlaugh.jpg",
        "description": "A plus-size man in a grey t-shirt, crouched in a grassy field laughing openly, holding a blade of grass, real green outdoor background",
        "baked_headline": None,
        "moment": "pure, unguarded joy outdoors - a strong feel-good opening image",
        "category": "weight_loss",
    },
    "ruststool": {
        "url": "/hero-images/ruststool.jpg",
        "description": "A plus-size man in a rust-orange sweater, sitting on a black stool laughing at his phone, one leg crossed up showing a white sneaker, warm dappled studio light",
        "baked_headline": None,
        "moment": "warm, relaxed, a good general lifestyle moment for almost any weight-loss email",
        "category": "weight_loss",
    },
    "windowespresso": {
        "url": "/hero-images/windowespresso.jpg",
        "description": "An older bearded man in a white linen band-collar shirt, sitting by a sunlit window holding a small espresso cup, a checkers board visible on the side table, content and relaxed",
        "baked_headline": None,
        "moment": "a quiet, settled, mature moment - good for an older-audience or routine-maintenance message",
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
    # --- General, round 2 - reviewed 2026-10-01, from the same
    # New_IMAGE_LIBRARY drop (two loose files sitting in its root, outside
    # the ASWL_SEPT26 weight-loss subfolder). Both are mature, average-build
    # men in calm everyday moments - genuinely usable for any category, but
    # not tagged weight_loss since neither shows the plus-size
    # representation that makes that category's own photos meaningfully
    # relevant to its real audience.
    "laptopathome": {
        "url": "/hero-images/laptopathome.jpg",
        "description": "A greying man with salt-and-pepper hair, working on a laptop balanced on his lap, relaxed on a low wooden stool in a warm minimal living room with a plant and a mug of coffee nearby",
        "baked_headline": None,
        "moment": "a calm working-from-home or settled-routine moment - fits any category needing an older/mature-audience lifestyle shot",
        "category": "general",
    },
    "phoneoffice": {
        "url": "/hero-images/phoneoffice.jpg",
        "description": "A man with a greying beard, glasses, and wireless earbuds, wearing a beige vest over a white t-shirt, smiling at his phone in a modern office/lounge setting",
        "baked_headline": None,
        "moment": "a light, everyday phone-check moment - good general opener for any category",
        "category": "general",
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
    silently returns an empty bank.

    REAL BIAS FIX: the returned dict's order is shuffled on every call, not
    HERO_BANK's own fixed definition order - copywriter_agent.py and
    creative_director_agent.py both build their hero catalog text straight
    from this dict's iteration order, and always presenting candidates in
    the exact same order on every single call risks a real, well-
    documented LLM failure mode (list-position bias: favouring an item
    because of where it sits in a long list, not because it genuinely fits
    best). Shuffling here, once, centrally, fixes it for every caller."""
    if category not in CATEGORIES:
        category = "hair_loss"
    matches = [
        (key, entry)
        for key, entry in HERO_BANK.items()
        if entry["category"] in (category, "general")
        and category not in entry.get("exclude_categories", [])
    ]
    random.shuffle(matches)
    return dict(matches)

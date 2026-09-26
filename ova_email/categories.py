"""OVA Singapore CRM categories - Contraception, Emergency Contraception,
Intimate Health, Weight Loss.

Sources, both real:
  - OVA_SG_CRM_AGENT_SCOPE.md (Thalia Bondoc, 7 Sep 2026) - the CRM agent's
    scope, the four categories, the market rules, the approved claims, the
    hard compliance rules.
  - OVA BRAND RESTAGE (May 2025) - the brand identity, the four tone-of-
    voice pillars, the doctor-written email format, and the real treatment
    portfolio (below, INTERNAL grounding only).

The four OVA tone-of-voice pillars apply to every category, on top of the
per-category voice notes:
  1. Smart not stuffy - talking to an informed adult; intelligent, never academic.
  2. Calm authority - straight-talking, rooted in care; the expert doesn't sell.
  3. Trustworthy not transactional - evidence-led, never salesy, never a hard push.
  4. Minimal and modern - plain language, sleek and unfussy; say less.

`internal_treatment_reference` is real portfolio detail from the brand deck,
for the agent's GROUNDING ONLY - so it knows what "the treatment" actually
is when it writes. It is NEVER customer-facing: hard rule SS4.2 bans naming
a prescription medicine or a specific product in Singapore outbound.

Singapore only. OVA is two different businesses by market - Singapore
offers contraception, emergency contraception and intimate health;
Malaysia does not. Never port a flow or a claim into a Malaysia-facing
surface.
"""

CATEGORIES = {
    "contraception": {
        "label": "Contraception",
        "otc_verified": False,
        "voice_notes": (
            "The retention engine of the business - she reorders for years once the refill habit sticks, "
            "so every touchpoint here protects the refill date, it does not sell. Write it as a short, "
            "matter-of-fact note from her care team: warm, routine, private, no euphemism and no shame. "
            "She is not in crisis. Treat this like any recurring healthcare habit. Convenience and "
            "discretion are the product; she stays in control."
        ),
        "internal_treatment_reference": (
            "INTERNAL ONLY, never name any of this: OVA's reproductive care is doctor-prescribed branded "
            "and generic birth control (pill and patch) dispensed by a licensed pharmacy, with an initial "
            "online doctor consultation and discreet delivery."
        ),
        "real_benefits": ["Doctor-led, 100% online", "Discreet delivery", "Cancel any time"],
        "objections": [
            "will this suit my body / side-effect worry",
            "cost of the initial consultation",
            "discretion of delivery and billing",
            "forgetting to reorder in time",
        ],
        "compliance_notes": (
            "The one real approved efficacy figure is method efficacy, not a product claim: 'up to 99% "
            "effective when taken regularly' - verbatim only, never a different number. Also approved: 'may "
            "help with acne, period regulation, period pain, PMS and PCOS symptoms.' Never name a product "
            "(see internal_treatment_reference). Never a discount percentage - a real dollar figure is "
            "fine. No medical advice, no suitability assessment, no dosing, no symptom-matching - the "
            "doctor decides. Any side-effect mention in a reply escalates straight to a doctor; no "
            "reassurance, no tips. Proof over promise: never overpromise, never emotional manipulation."
        ),
        "discretion_level": "standard",
    },
    "emergency_contraception": {
        "label": "Emergency Contraception",
        "otc_verified": False,
        "voice_notes": (
            "Speed is the entire product. She is anxious and in a hurry and will go elsewhere within "
            "minutes if the reply is slow or asks her anything other than how fast she can see a doctor. "
            "Urgent and neutral. Never comment on timing, never comment on effectiveness, never ask what "
            "happened. The whole job is getting her to a doctor as fast as possible."
        ),
        "internal_treatment_reference": (
            "INTERNAL ONLY, never name it: a doctor-prescribed single-dose emergency contraceptive, "
            "dispensed by a licensed pharmacy after a fast online consultation."
        ),
        "real_benefits": ["See a doctor fast", "Private and discreet", "Doctor-led, 100% online"],
        "objections": [
            "how fast can I actually be seen",
            "will this still work",
            "being asked to explain myself",
            "cost",
        ],
        "compliance_notes": (
            "HARD RULE, this category only: never ask what happened, and never comment on timing or "
            "effectiveness - ask how quickly she needs to speak to a doctor, then get her there. Never "
            "name a product. No medical advice, no suitability assessment, no dosing. Any side-effect "
            "mention escalates straight to a doctor; no reassurance, no tips."
        ),
        "discretion_level": "high",
    },
    "intimate_health": {
        "label": "Intimate Health",
        "otc_verified": False,
        "voice_notes": (
            "Consultation-led and high-intent - she has a real, time-sensitive concern and wants a "
            "doctor's read on it quickly. Warm, private, non-judgemental, matter-of-fact - the same "
            "register as contraception, never clinical-cold and never coy. Often a natural bridge into an "
            "ongoing contraception conversation afterwards, but never force that pivot inside this "
            "touchpoint."
        ),
        "internal_treatment_reference": (
            "INTERNAL ONLY: consultation-led intimate/sexual health care - a doctor reviews her concern "
            "online and, where appropriate, prescribes; a licensed pharmacy dispenses. No consumer "
            "product to name."
        ),
        "real_benefits": ["Doctor-led, 100% online", "Private and discreet", "Real answers, no waiting rooms"],
        "objections": [
            "is this normal / should I be worried",
            "discretion of a consult and delivery",
            "cost",
            "being embarrassed",
        ],
        "compliance_notes": (
            "No approved efficacy or outcome claims exist for this category - describe the consult-led "
            "pathway only, never a product benefit. Never name a product. No medical advice, no "
            "suitability assessment, no symptom-matching - book the doctor, don't diagnose. Any symptom "
            "detail volunteered in a reply escalates straight to a doctor; no reassurance, no tips."
        ),
        "discretion_level": "high",
    },
    "weight_loss": {
        "label": "Weight Loss",
        "otc_verified": True,
        "voice_notes": (
            "OVA's hero category. Doctor-led, delivered discreetly in plain packaging, everything online, "
            "cancel any time. Motivational and practical, never aspirational-body framing: this is a "
            "medical programme for long-term change, not a short-term fix. The basket and package play "
            "(three-month packages save real, specific dollars over paying month to month) and the retail "
            "attachments (a scale, booster supplements) close on the same message, never a separate "
            "hard-sell follow-up."
        ),
        "internal_treatment_reference": (
            "INTERNAL ONLY, never name any of this: a holistic doctor-led weight loss programme - a "
            "once-weekly injectable GLP-1 medication, booster supplements, a smart BMI weighing scale, "
            "protein powder, and unlimited on-demand doctor consultations. The programme is designed to "
            "help her lose fat, not muscle."
        ),
        "real_benefits": ["Doctor-led programme", "Unlimited doctor consultations", "Discreet delivery, plain packaging", "Cancel any time"],
        "objections": [
            "does it work for me specifically",
            "side effects",
            "cost and the size of the commitment",
            "have I failed at this before",
            "is it safe",
        ],
        "compliance_notes": (
            "Real approved claims (verbatim only, never invented, paraphrased, or upgraded): 'lose up to "
            "22.5% of your body weight' (the dual-action weekly option), 'lose 15%' (the single-action "
            "weekly option), 'lose 5 to 10%' (the daily tablet option), 'regulates appetite and reduces "
            "food noise', '7.5x more weight lost than diet and exercise alone'. Never name a medicine - "
            "describe the format generically ('the weekly injectable option') only if a distinction is "
            "genuinely needed. Package savings are always a real DOLLAR figure (S$137 to S$286 by "
            "strength), never a percentage. No before/after framing, no body comparison, no aspirational- "
            "body imagery, no body-shaming however gentle. No BMI-based framing or self-assessment "
            "language. No medical advice or suitability assessment - the doctor decides; any side-effect "
            "mention escalates straight to a doctor, no reassurance, no tips."
        ),
        "discretion_level": "standard",
    },
}

VALID_CATEGORY_SLUGS = list(CATEGORIES.keys())
DEFAULT_CATEGORY = "contraception"


def category_notes_text(category: str) -> str:
    """Real per-category context block injected into the Copywriter/Sweeper
    prompts. Falls back to Contraception (the retention-engine category)
    for an unrecognised slug - never a category-less prompt."""
    cat = CATEGORIES.get(category, CATEGORIES[DEFAULT_CATEGORY])
    objections = ", ".join(cat["objections"])
    benefits = ", ".join(cat.get("real_benefits", []))
    return (
        f"CATEGORY FOR THIS EMAIL: {cat['label']}.\n"
        f"{cat['voice_notes']}\n"
        f"{cat['internal_treatment_reference']}\n"
        f"REAL, APPROVED SERVICE BENEFITS for this category (the ONLY source for any benefit-style line "
        f"in an icon_grid, two_column, or checklist_card block - never invent a different one): {benefits}.\n"
        f"Real objections this reader is likely weighing: {objections}.\n"
        f"CATEGORY-SPECIFIC COMPLIANCE: {cat['compliance_notes']}"
    )

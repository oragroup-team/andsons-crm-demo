"""Canonical andSons Hair Loss lifecycle flow definitions.

Source of truth: the real andSons CRM knowledge base
(03-Segments-Lifecycle.md, 07-Patient-Journey.md, 06-MoEngage-Data-Dictionary.md)
in CRM_Email_Generation_Data/&SONS CRM Knowledge. These are the 10 live
MoEngage flows plus P2 Consult No-Show (added by Thalia, not one of the
original 10), staged in the priority order she set on 2026-07-06:
P1 -> P2 -> P3 -> The Valley -> Consult Booking -> Replenishment/Dunning ->
Rx-Not-Suitable-OTC -> AOV Growth -> Win-back -> Results & Milestone ->
Quiz Recovery.

`track` gates what the Copywriter/Sweeper allow:
  - "rx"      Rx track — never name prescription medicines, never a price.
  - "otc"     OTC track — Redensyl/Trio/Kit may be named with real prices.
  - "neutral" Neither product nor price is the point of this touchpoint.
"""

FLOWS = [
    {
        "slug": "p1_plan_not_purchased",
        "label": "P1 - Plan Created, Not Purchased",
        "track": "rx",
        "priority": 1,
        "trigger": "treatment_plan_created (exit: paid_approved)",
        "audience": "Had the online teleconsultation, the doctor already explained his personalised plan, and he chose to pay later. He is warm and informed, not a stranger to his plan.",
        "goal": "Help him complete a deferred payment decision he has already half-made, with reassurance and low friction. Never a reveal, never a sell.",
        "cta": "Start My Treatment",
        "allow_price": False,
        "allow_stat": False,
    },
    {
        "slug": "p2_consult_no_show",
        "label": "P2 - Consult No-Show",
        "track": "rx",
        "priority": 2,
        "trigger": "medical_consultation_updated (status = no_show)",
        "audience": "Booked a free online consultation with a licensed Singapore doctor and didn't attend.",
        "goal": "Get him to rebook with zero pressure or judgement - life happens, the doctor is ready when he is.",
        "cta": "Rebook My Consultation",
        "allow_price": False,
        "allow_stat": False,
    },
    {
        "slug": "p3_otc_cart_abandon",
        "label": "P3 - OTC Cart Abandon",
        "track": "otc",
        "priority": 3,
        "trigger": "add_to_cart (no purchase in window)",
        "audience": "Added an OTC product (Redensyl serum, Trio, or Kit) to cart but didn't check out.",
        "goal": "Recover the sale quickly and simply - the product is exactly where he left it.",
        "cta": "Complete My Order",
        "allow_price": True,
        "allow_stat": True,
    },
    {
        "slug": "the_valley",
        "label": "The Valley (Month 1-3)",
        "track": "neutral",
        "priority": 4,
        "trigger": "subscription month 1-3, pre-results",
        "audience": "One to three months into treatment with no visible results yet - the highest churn-risk window.",
        "goal": "Normalise shedding and no visible change as expected and reinforce staying consistent with the plan.",
        "cta": "See My Treatment Timeline",
        "allow_price": False,
        "allow_stat": True,
    },
    {
        "slug": "consult_booking",
        "label": "Consult Booking",
        "track": "neutral",
        "priority": 5,
        "trigger": "quiz_completed (without appointment_scheduled)",
        "audience": "Finished the hair loss assessment but hasn't booked the free online doctor consultation yet.",
        "goal": "De-stigmatise the online consult - it is private, free, and takes 10 to 15 minutes with a licensed Singapore doctor.",
        "cta": "Book My Free Consultation",
        "allow_price": False,
        "allow_stat": False,
    },
    {
        "slug": "replenishment_dunning",
        "label": "Replenishment / Dunning",
        "track": "neutral",
        "priority": 6,
        "trigger": "subscription_renewal_date_changed / subscription_payment_updated (failed) / pause or skip",
        "audience": "Subscription renewal is coming up, a recent payment failed, or he paused or skipped a delivery.",
        "goal": "Protect the recurring order with a calm, practical reminder - never guilt, never pressure.",
        "cta": "Confirm My Next Delivery",
        "allow_price": False,
        "allow_stat": False,
    },
    {
        "slug": "rx_not_suitable_otc",
        "label": "Rx Not Suitable -> OTC",
        "track": "otc",
        "priority": 7,
        "trigger": "doctor rules Rx unsuitable",
        "audience": "The doctor reviewed his case and ruled prescription treatment isn't suitable for him right now.",
        "goal": "Retain him with the Redensyl serum, positioned as a genuine evidence-backed first choice, never a consolation prize.",
        "cta": "Start My Redensyl Routine",
        "allow_price": True,
        "allow_stat": True,
    },
    {
        "slug": "aov_growth",
        "label": "AOV Growth",
        "track": "otc",
        "priority": 8,
        "trigger": "OTC serum-only, 30+ days adherent",
        "audience": "An OTC serum-only subscriber for 30 or more days, consistent and adherent with his routine.",
        "goal": "Suggest building around the serum with the Trio or Kit as a natural next step, never a hard upsell.",
        "cta": "Upgrade My Routine",
        "allow_price": True,
        "allow_stat": True,
    },
    {
        "slug": "winback",
        "label": "Win-back",
        "track": "neutral",
        "priority": 9,
        "trigger": "initiated_subscription_cancel -> subscription_cancellation_confirmed",
        "audience": "Cancelled or lapsed, for one of three reasons: didn't see results, cost, or simply forgot.",
        "goal": "Reactivate by speaking to his specific reason for leaving - reframe the results timeline, offer a simpler way back, or a low-effort resume. No pricing figures until finance verifies the numbers.",
        "cta": "Restart My Treatment",
        "allow_price": False,
        "allow_stat": True,
    },
    {
        "slug": "results_milestone",
        "label": "Results & Milestone (Month 4+)",
        "track": "neutral",
        "priority": 10,
        "trigger": "subscriber month 4+",
        "audience": "Four or more months into treatment and starting to see real change.",
        "goal": "Reinforce his progress and invite him to share it - the tone is proud, not salesy.",
        "cta": "See My Progress",
        "allow_price": False,
        "allow_stat": True,
    },
    {
        "slug": "quiz_recovery",
        "label": "Quiz Recovery",
        "track": "neutral",
        "priority": 11,
        "trigger": "start_quiz / quiz_progress_update (without quiz_completed)",
        "audience": "Started the hair loss assessment quiz but didn't finish it.",
        "goal": "Recover the intent gently - his answers are saved, and finishing takes about two minutes.",
        "cta": "Continue My Assessment",
        "allow_price": False,
        "allow_stat": False,
    },
]

FLOW_BY_SLUG = {f["slug"]: f for f in FLOWS}
VALID_FLOW_SLUGS = [f["slug"] for f in FLOWS]

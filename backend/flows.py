"""Canonical andSons Hair Loss lifecycle flow definitions.

Source of truth: the real andSons CRM knowledge base
(03-Segments-Lifecycle.md, 07-Patient-Journey.md, 06-MoEngage-Data-Dictionary.md,
"MoEngage Setup - P1 Build Packet (for Joanne).md", "Agent Prompts - CRM Team.md")
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

`cadence`: each flow is a real multi-touchpoint MoEngage journey, not one
email - this is the actual sequence (channel + timing + intent) MoEngage
Ops builds. Two real sources feed it:
  - P1's cadence is the exact, fully-specified sequence from the real
    "MoEngage Setup - P1 Build Packet" (5 touchpoints, timings 0/12h/2d/7d/21d).
  - The other 9 original flows' cadences come from 03-Segments-Lifecycle.md's
    real cadence table, which still lists "Push" as a channel from an
    earlier planning pass. "Agent Prompts - CRM Team.md" (one day newer,
    and the live n8n system prompt every agent actually runs on) corrects
    this explicitly and repeatedly: andSons has no app, so push notifications
    and SMS do not exist - only Email and WhatsApp are real channels. Every
    "Push" step below is mapped to WhatsApp (the real replacement channel
    for a short, timely nudge) for that reason - never invented, just
    reconciled against the more authoritative, more recent source.
  - P2 (Consult No-Show) has a documented trigger/exit but no separately
    designed cadence anywhere in the knowledge base - the P1 build packet
    explicitly says to "reuse this structure" for the other priority flows.
    Its cadence here is a shorter adaptation of that same real template
    (immediate email, a WhatsApp nudge, a follow-up email), not a
    fabrication of numbers that don't exist.
  - Replenishment/Dunning and Win-back are event-branched in the real docs
    (different touchpoints fire depending on which sub-event happens, not
    one fixed timeline) - their cadence lists represent that branching
    honestly, each step's timing naming which case it belongs to.
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
        "cadence": [
            {"n": 1, "channel": "email", "timing": "immediate", "intent": "Your treatment plan is ready - warm, plan-ready, low-friction start."},
            {"n": 2, "channel": "whatsapp", "timing": "+12h", "intent": "Short personal nudge, one link, timely reminder."},
            {"n": 3, "channel": "email", "timing": "+2 days", "intent": "Why starting earlier matters - doctor-led stakes: hair loss is progressive."},
            {"n": 4, "channel": "email", "timing": "+7 days", "intent": "What's still protectable today - calmly sharpen protect-now vs wait."},
            {"n": 5, "channel": "whatsapp", "timing": "+21 days", "intent": "Final low-pressure touch, a fresh angle, then exit the flow."},
        ],
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
        "cadence": [
            {"n": 1, "channel": "email", "timing": "immediate", "intent": "No judgement, life happens - here's your rebooking link."},
            {"n": 2, "channel": "whatsapp", "timing": "+24h", "intent": "Short nudge if he hasn't rebooked yet, doctor is ready when he is."},
            {"n": 3, "channel": "email", "timing": "+4 days", "intent": "Final low-pressure reminder, then exit the flow."},
        ],
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
        "cadence": [
            {"n": 1, "channel": "email", "timing": "+1h", "intent": "Recover the sale quickly - the product is exactly where he left it."},
            {"n": 2, "channel": "whatsapp", "timing": "+24h", "intent": "Short reminder nudge if the cart is still sitting there."},
        ],
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
        "cadence": [
            {"n": 1, "channel": "email", "timing": "Day 30", "intent": "Normalise shedding and no visible change yet, reinforce consistency."},
            {"n": 2, "channel": "whatsapp", "timing": "Day 45", "intent": "Short check-in nudge, still early, keep going."},
            {"n": 3, "channel": "email", "timing": "Day 55", "intent": "Reinforce the real results timeline, first changes often around Month 3."},
            {"n": 4, "channel": "email", "timing": "Day 75", "intent": "Closing encouragement as he nears the results window."},
        ],
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
        "cadence": [
            {"n": 1, "channel": "email", "timing": "+1h", "intent": "De-stigmatise the consult - private, free, 10-15 minutes with a licensed doctor."},
            {"n": 2, "channel": "whatsapp", "timing": "+24h", "intent": "Short nudge if he hasn't booked yet."},
            {"n": 3, "channel": "email", "timing": "+2 days", "intent": "Final reminder, then exit the flow."},
        ],
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
        # Event-branched, not a single timeline (03-Segments-Lifecycle.md):
        # different touchpoints fire depending on which sub-event happens.
        "cadence": [
            {"n": 1, "channel": "email", "timing": "-3 days before dispatch (renewal reminder)", "intent": "Calm heads-up that the next delivery is coming, nothing to do unless he wants to change something."},
            {"n": 2, "channel": "whatsapp", "timing": "on payment failure", "intent": "Practical, judgement-free nudge that a payment didn't go through."},
            {"n": 3, "channel": "email", "timing": "on payment failure", "intent": "Same payment-fail case, fuller detail and a clear fix-it link."},
            {"n": 4, "channel": "email", "timing": "on pause or skip", "intent": "Acknowledge the pause/skip plainly, no guilt, door open to resume."},
        ],
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
        "cadence": [
            {"n": 1, "channel": "email", "timing": "immediate", "intent": "Reposition Redensyl as a genuine evidence-backed first choice, never a consolation prize."},
            {"n": 2, "channel": "email", "timing": "+2 days", "intent": "Reinforce the choice with the real clinical backing, one CTA."},
        ],
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
        # Second touchpoint is conditional (only if he browsed the
        # Trio/Kit without buying) - included here so the whole real
        # sequence is visible, labelled with its real trigger condition.
        "cadence": [
            {"n": 1, "channel": "email", "timing": "on trigger (30+ days adherent)", "intent": "Suggest building around the serum with the Trio or Kit as a natural next step, never a hard upsell."},
            {"n": 2, "channel": "whatsapp", "timing": "if he browsed the Trio/Kit without buying", "intent": "Short, light nudge back to what he was already looking at."},
        ],
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
        # Segmented by churn-reason bucket (03-Segments-Lifecycle.md), not
        # one linear timeline - each bucket gets its own single touchpoint
        # speaking to that specific reason for leaving.
        "cadence": [
            {"n": 1, "channel": "email", "timing": "bucket: did not see results", "intent": "Reframe the results timeline - first changes often start around Month 3."},
            {"n": 2, "channel": "email", "timing": "bucket: too expensive", "intent": "A clearer, flexible way back - no pricing figures until finance verifies them."},
            {"n": 3, "channel": "whatsapp", "timing": "bucket: forgot / lapsed", "intent": "Resume in one tap - light, low-effort nudge."},
        ],
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
        "cadence": [
            {"n": 1, "channel": "email", "timing": "Month 4", "intent": "Reinforce real progress, proud tone, invite him to share it."},
            {"n": 2, "channel": "whatsapp", "timing": "Month 4", "intent": "Short warm congratulations nudge."},
            {"n": 3, "channel": "email", "timing": "Month 5", "intent": "Continue the momentum, light UGC/referral invite."},
        ],
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
        "cadence": [
            {"n": 1, "channel": "email", "timing": "+1h", "intent": "Recover the intent gently - answers are saved, finishing takes about two minutes."},
            {"n": 2, "channel": "whatsapp", "timing": "+24h", "intent": "Short nudge if the quiz is still unfinished."},
            {"n": 3, "channel": "email", "timing": "+3 days", "intent": "Final low-pressure reminder, then exit the flow."},
        ],
    },
]

FLOW_BY_SLUG = {f["slug"]: f for f in FLOWS}
VALID_FLOW_SLUGS = [f["slug"] for f in FLOWS]

"""OVA Singapore lifecycle flow definitions.

Source: OVA_SG_CRM_AGENT_SCOPE.md SS6 (Lifecycle map), itself sourced from
the real ORA Lead Gen Sales Playbook - September 2026. Each flow below is
one arrow/stage from that map, turned into a real, runnable flow definition
- the same role flows.py plays in the andSons backend.

REAL, STATED LIMITATION (scope doc SS6/SS8A, carried over honestly rather
than smoothed over): this is a DESIGNED TARGET STATE, not an audit of a live
OVA SG MoEngage workspace - the scope doc is explicit that OVA SG workspace
API credentials were never available, so (unlike andSons' flows.py, which
cross-checked its cadences against 600 real live MoEngage campaigns) none of
the timings/channels below are independently verified against a live
account. Every cadence here is deliberately short (1-2 touchpoints) and
conservative rather than inventing a longer, more elaborate sequence with no
real evidence behind it.

CHANNELS - EMAIL AND WHATSAPP, matching andSons exactly: the same two real
channels the andSons Email bot uses, no push notifications (andSons has
none either - see andSons' flows.py's own module docstring for why every
"push" step there was corrected back to "whatsapp").

`track` (rx / otc / neutral): same meaning as andSons' flows.py - "rx" is a
doctor-led treatment decision (never imply the customer can self-stop or
self-change it), "otc" is a real non-prescription retail attachment
(supplements, the BMI scale), "neutral" is neither.

`price_context`: None means no price/dollar figure of any kind is
permitted on this flow. A string is the ONE real, approved dollar figure
(never a percentage - hard rule SS4.3) the Copywriter may optionally use,
copied verbatim from the scope doc - never a different number, and never a
named product alongside it (hard rule SS4.2: no prescription medicine named
in Singapore outbound, ever).

`allow_stat`: whether this flow's moment is a genuine fit for restating one
of categories.py's real approved claims (contraception's 99% efficacy
figure, or one of weight_loss's real percentage-loss claims) - most
logistics-only flows (a reminder, a rebook nudge) do not need one and
should stay allow_stat=False so the Copywriter isn't reaching for a claim
that doesn't actually belong in that specific moment.
"""

FLOWS = [
    # --- Contraception - SS6.1, the retention engine -----------------------
    {
        "slug": "contraception_consult_reminder",
        "label": "Contraception - Consult Reminder",
        "category": "contraception",
        "track": "rx",
        "priority": 1,
        "trigger": "medical_consultation_booked (contraception)",
        "audience": "Booked the initial online contraception consultation and hasn't attended yet.",
        "goal": "Reduce no-shows with a calm day-before and hour-before reminder - routine, not urgent.",
        "cta": "confirm your consultation time",
        "price_context": "the S$20 initial consultation fee",
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "day before", "intent": "Calm heads-up the consult is tomorrow - routine, nothing to prepare."},
            {"n": 2, "channel": "whatsapp", "timing": "1 hour before", "intent": "Short, practical reminder the consult is starting soon."},
        ],
    },
    {
        "slug": "contraception_no_show_rebook",
        "label": "Contraception - Consult No-Show",
        "category": "contraception",
        "track": "rx",
        "priority": 2,
        "trigger": "medical_consultation_updated (status = no_show, contraception)",
        "audience": "Booked a contraception consult with a licensed Singapore doctor and didn't attend.",
        "goal": "Get her to rebook the same day, with zero judgement - life happens.",
        "cta": "rebook your consultation",
        "price_context": None,
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "whatsapp", "timing": "same day", "intent": "No judgement, life happens - the doctor is ready whenever she is."},
        ],
    },
    {
        "slug": "contraception_prerefill_nudge",
        "label": "Contraception - Pre-Refill Nudge",
        "category": "contraception",
        "track": "neutral",
        "priority": 3,
        "trigger": "subscription_refill_date_approaching",
        "audience": "Active contraception subscriber whose next refill date is coming up.",
        "goal": "Protect the refill date with a calm, practical heads-up - nothing to do unless she wants to change something.",
        "cta": "confirm your next refill",
        "price_context": None,
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "-5 days before refill", "intent": "Calm heads-up the next refill is coming, easy to confirm or adjust."},
        ],
    },
    {
        "slug": "contraception_missed_refill",
        "label": "Contraception - Missed Refill",
        "category": "contraception",
        "track": "neutral",
        "priority": 4,
        "trigger": "refill_date_passed (not renewed)",
        "audience": "Her refill date came and went without renewing - contacted the day it's due, not weeks later.",
        "goal": "Recover the refill quickly, plainly, no guilt - the real retention lever for this whole category.",
        "cta": "reorder your refill",
        "price_context": None,
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "on the due date", "intent": "Practical, judgement-free nudge that today's refill hasn't gone through yet."},
            {"n": 2, "channel": "whatsapp", "timing": "+2 days", "intent": "Short follow-up if she still hasn't reordered."},
        ],
    },
    {
        "slug": "contraception_lapsed_winback",
        "label": "Contraception - Lapsed Winback",
        "category": "contraception",
        "track": "rx",
        "priority": 5,
        "trigger": "subscription_cancelled / long-lapsed refill",
        "audience": "Cancelled, or let refills lapse long enough that recovery is getting harder.",
        "goal": "Reactivate plainly and warmly - recovery gets harder every week, so speak to it now, not later.",
        "cta": "restart your contraception",
        "price_context": "the S$20 initial consultation fee",
        "allow_stat": True,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "on lapse", "intent": "Warm, plain invitation back in - restarting is as simple as the first time."},
        ],
    },
    {
        "slug": "contraception_side_effect_churn_save",
        "label": "Contraception - Side-Effect / Switch Churn-Save",
        "category": "contraception",
        "track": "rx",
        "priority": 6,
        "trigger": "customer mentions a side effect or wanting to switch",
        "audience": "Mentioned a side effect, or that she's thinking about switching or stopping.",
        "goal": "Route straight to the doctor - never answered directly, never reassured, never given tips.",
        "cta": "talk to a doctor",
        "price_context": None,
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "immediate", "intent": "Get her in front of a doctor about this specific concern - no reassurance, no home-remedy tips, nothing diagnostic."},
        ],
    },
    # --- Emergency Contraception - SS6.2, speed is the whole product -------
    {
        "slug": "ec_fast_response",
        "label": "Emergency Contraception - Fast Response",
        "category": "emergency_contraception",
        "track": "rx",
        "priority": 7,
        "trigger": "EC page visit / lead / inquiry",
        "audience": "Needs emergency contraception and is anxious and in a hurry.",
        "goal": "Get her in front of a doctor within minutes - never ask what happened, never comment on timing or effectiveness.",
        "cta": "speak to a doctor now",
        "price_context": None,
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "whatsapp", "timing": "immediate (minutes)", "intent": "The single fastest possible path to a doctor - nothing else in this message."},
        ],
    },
    {
        "slug": "ec_post_purchase_bridge",
        "label": "Emergency Contraception - Ongoing Contraception Bridge",
        "category": "emergency_contraception",
        "track": "rx",
        "priority": 8,
        "trigger": "EC consultation completed",
        "audience": "Just had an emergency contraception consult - a real, named CRM objective is converting her to ongoing contraception within 14 days.",
        "goal": "Bridge to ongoing contraception gently, within the 14-day window - never pushy, never referencing the EC visit's specifics.",
        "cta": "explore ongoing contraception",
        "price_context": "the S$20 initial consultation fee",
        "allow_stat": True,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "+3 days", "intent": "A calm, unhurried introduction to ongoing contraception as an easier path forward."},
            {"n": 2, "channel": "whatsapp", "timing": "+10 days", "intent": "Light final nudge before the 14-day window closes."},
        ],
    },
    # --- Intimate Health - SS6.3, consultation-led --------------------------
    {
        "slug": "intimate_health_consult_entry",
        "label": "Intimate Health - Consult Entry",
        "category": "intimate_health",
        "track": "rx",
        "priority": 9,
        "trigger": "intimate health inquiry / quiz completed",
        "audience": "Has a private, time-sensitive intimate health concern and hasn't booked a consult yet.",
        "goal": "Get the consult booked, discreetly - high-intent, so keep it short and direct.",
        "cta": "book your consultation",
        "price_context": None,
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "immediate", "intent": "Discreet, direct invitation to book the consult - private, not embarrassing."},
        ],
    },
    {
        "slug": "intimate_health_post_consult_bridge",
        "label": "Intimate Health - Post-Consult Bridge",
        "category": "intimate_health",
        "track": "rx",
        "priority": 10,
        "trigger": "intimate health consultation completed",
        "audience": "Just had an intimate health consult.",
        "goal": "A natural bridge into ongoing contraception where it genuinely fits - never forced.",
        "cta": "see what is next for you",
        "price_context": None,
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "+5 days", "intent": "Check in on how things are going, and mention ongoing contraception only if it genuinely follows naturally."},
        ],
    },
    # --- Weight Loss - SS6.4, the basket and package play -------------------
    {
        "slug": "weight_loss_consult_booking",
        "label": "Weight Loss - Consult Booking",
        "category": "weight_loss",
        "track": "rx",
        "priority": 11,
        "trigger": "quiz_completed (weight loss, without consult booked)",
        "audience": "Finished the weight loss assessment but hasn't booked the consult yet.",
        "goal": "Get the consult booked - doctor-led, discreet, everything online.",
        "cta": "book your consultation",
        "price_context": None,
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "+1h", "intent": "Warm invitation to book the consult - doctor-led, private, done from home."},
            {"n": 2, "channel": "whatsapp", "timing": "+24h", "intent": "Short nudge if she hasn't booked yet."},
        ],
    },
    {
        "slug": "weight_loss_noshow_recovery",
        "label": "Weight Loss - Consult No-Show",
        "category": "weight_loss",
        "track": "rx",
        "priority": 12,
        "trigger": "medical_consultation_updated (status = no_show, weight loss)",
        "audience": "Booked a weight loss consult and didn't attend.",
        "goal": "Rebook with zero judgement.",
        "cta": "rebook your consultation",
        "price_context": None,
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "whatsapp", "timing": "same day", "intent": "No judgement, life happens - the doctor is ready whenever she is."},
        ],
    },
    {
        "slug": "weight_loss_package_offer",
        "label": "Weight Loss - Package Offer",
        "category": "weight_loss",
        "track": "otc",
        "priority": 13,
        "trigger": "treatment plan created, package decision pending",
        "audience": "Doctor confirmed a plan and is deciding between paying month to month or a three-month package.",
        "goal": "Offer the three-month package first, real dollar saving stated plainly; single month as a genuine fallback, never framed as second-best.",
        "cta": "choose your package",
        "price_context": "the real three-month package saving (S$137 to S$286 depending on strength) - state the dollar figure, never a percentage",
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "immediate", "intent": "Lead with the three-month package and its real dollar saving; mention the single-month option as a genuine, no-pressure fallback."},
            {"n": 2, "channel": "whatsapp", "timing": "+2 days", "intent": "Short nudge if she hasn't chosen a package yet."},
        ],
    },
    {
        "slug": "weight_loss_retail_attachment",
        "label": "Weight Loss - Retail Attachment",
        "category": "weight_loss",
        "track": "otc",
        "priority": 14,
        "trigger": "treatment order placed, no retail attachment yet",
        "audience": "Just placed a weight loss treatment order.",
        "goal": "Offer the Smart BMI Scale / CoQ10 / booster attachments on the same order, one message, never a separate hard-sell later.",
        "cta": "add the extras to your order",
        "price_context": "the real retail attachment prices (Smart BMI Weighing Scale S$59, CoQ10 S$50, Chromium/B12/D3 boosters S$25 each)",
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "same order window", "intent": "Simple, one-message add-on offer alongside the order she just placed."},
        ],
    },
    {
        "slug": "weight_loss_adherence_support",
        "label": "Weight Loss - Adherence Support (Month 1-3)",
        "category": "weight_loss",
        "track": "neutral",
        "priority": 15,
        "trigger": "subscriber month 1-3",
        "audience": "One to three months into treatment.",
        "goal": "Normalise the real timeline and keep her adherent to the plan.",
        "cta": "see your treatment timeline",
        "price_context": None,
        "allow_stat": True,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "Day 30", "intent": "Reinforce the real timeline and encourage staying consistent with the plan."},
            {"n": 2, "channel": "whatsapp", "timing": "Day 60", "intent": "Short encouragement check-in."},
        ],
    },
    {
        "slug": "weight_loss_stepup_refill",
        "label": "Weight Loss - Step-Up / Refill",
        "category": "weight_loss",
        "track": "otc",
        "priority": 16,
        "trigger": "subscription_renewal_date_changed / dose step-up decision",
        "audience": "Adherent subscriber approaching a refill or a doctor-guided dose step-up.",
        "goal": "Confirm the next delivery, or the doctor's step-up decision, calmly and practically.",
        "cta": "confirm your next delivery",
        "price_context": None,
        "allow_stat": False,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "-3 days before dispatch", "intent": "Calm heads-up the next delivery is coming, easy to confirm or adjust."},
        ],
    },
    {
        "slug": "weight_loss_lapse_winback",
        "label": "Weight Loss - Lapse Winback",
        "category": "weight_loss",
        "track": "neutral",
        "priority": 17,
        "trigger": "initiated_subscription_cancel -> subscription_cancellation_confirmed",
        "audience": "Cancelled or lapsed from the weight loss programme.",
        "goal": "Win back with the real package saving and a real approved claim as reminders, never pressure.",
        "cta": "restart your programme",
        "price_context": "the real three-month package saving (S$137 to S$286 depending on strength) - state the dollar figure, never a percentage",
        "allow_stat": True,
        "cadence": [
            {"n": 1, "channel": "email", "timing": "on lapse", "intent": "Warm, low-pressure invitation back in, grounded in the real programme benefits and savings."},
        ],
    },
]


class _LazyFlowCatalog(dict):
    """Firestore-backed catalog for any flow head_of_crm_agent.
    synthesize_flow_for_signal() designs at runtime - ported from andSons'
    flows.py unchanged (see that file's own docstring for the real,
    live-caught multi-instance bug this fixes). Reads via
    session_store.get_synthesized_flow(), which - see this app's own
    session_store.py - uses the `ova_synthesized_flows` collection, kept
    separate from andSons' own `synthesized_flows` collection even when
    both apps share one Firestore project."""

    def __missing__(self, slug):
        from session_store import get_synthesized_flow  # local import: avoids a hard Firestore dependency at module load
        flow = get_synthesized_flow(slug)
        if flow is None:
            raise KeyError(slug)
        self[slug] = flow
        if slug not in VALID_FLOW_SLUGS:
            VALID_FLOW_SLUGS.append(slug)
        return flow

    def get(self, slug, default=None):
        try:
            return self[slug]
        except KeyError:
            return default


FLOW_BY_SLUG = _LazyFlowCatalog((f["slug"], f) for f in FLOWS)
VALID_FLOW_SLUGS = [f["slug"] for f in FLOWS]

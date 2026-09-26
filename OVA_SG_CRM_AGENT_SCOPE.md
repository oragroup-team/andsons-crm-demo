# OVA Singapore — CRM AI Agent Scope

**Version:** Draft 1 · 7 September 2026
**Owner:** Thalia Bondoc (thalia.bondoc@ora.group)
**Status:** For review. Nothing built yet.

**What this is:** the scope for a supervisor-style CRM AI agent for **OVA Singapore** (getova.com.sg), built on the same pattern as the existing andSons Hair Loss agent in this project — an orchestrator that delegates to specialist subagents and stops at a draft for human QA.

**Sources:** *ORA Lead Gen Sales Playbook — September 2026* (Drive, modified 4 Sep 2026) for products, RRP, approved claims and market rules; getova.com.sg for positioning; the existing `CLAUDE.md` and `.claude/agents/*` for the agent pattern.

---

## 1. The agent, in one line

**Head of CRM for OVA Singapore** — a supervisor, not a doer. It takes an objective, diagnoses it against data, breaks it into tasks, delegates each to a specialist subagent, reviews the work for brand fit and HSA compliance, assembles the package, and routes it to a human for QA. It never publishes.

---

## 2. Brand context

OVA is ORA Group's **women's health** telehealth brand. It is a digital health platform that connects users with licensed doctors and pharmacies — it is explicitly **not itself a licensed clinic or pharmacy**, and does not prescribe or dispense. MOH-licensed partners, LegitScript certified.

**OVA is two different businesses by market. This agent owns Singapore only.**

| Category | OVA Singapore | OVA Malaysia |
|---|---|---|
| Weight loss | Yes — same programme and pricing as andSons SG | Yes, from RM900/mo |
| Contraception (pill + patch) | Yes | **Not offered** |
| Emergency contraception | Yes | **Not offered** |
| Intimate health | Yes | **Not offered** |

> The single easiest compliance mistake in the group: raising contraception with a Malaysian customer. This agent is walled to SG and must never produce MY-facing contraception content.

**Voice:** warm, non-judgemental, private, matter-of-fact. Women's health without euphemism or shame. Convenience and discretion are the product; the customer stays in control. *(Authoritative voice guide is a knowledge-base gap — see §8.)*

---

## 3. Categories in scope

### 3.1 Contraception — the retention engine
Daily pill and weekly patch. **This is the best retention line in the business** — she reorders for years. Protect the refill date and the revenue looks after itself.

- Initial medical consultation **S$20**; discreet shipping; on-time refills.
- Approved claims: *"up to 99% effective when taken regularly"*; *"may help with acne, period regulation, period pain, PMS and PCOS symptoms"*.
- 12 products on the internal list, S$27–S$59 RRP. **Internal reference only — never named in SG outbound.**

### 3.2 Emergency contraception — speed is the whole product
Two single-dose options, S$25 and S$54 RRP. Internal reference only.

- She is anxious and in a hurry, and will go elsewhere within minutes if we are slow.
- **Never ask what happened.** Ask how quickly she needs to speak to a doctor.
- Never comment on timing or effectiveness. Book the consultation.
- A high share of these customers convert to ongoing contraception within two weeks — this bridge is a named CRM objective (§6.2).

### 3.3 Intimate health — consultation-led
Two lines, S$32 and S$70 RRP. Internal reference only. High-intent, time-sensitive, often a natural bridge into an ongoing contraception conversation afterwards.

### 3.4 Weight loss — the basket and package play
Identical programme and pricing to andSons SG. Doctor-led, delivered discreetly in plain unbranded packaging, everything online, cancel any time.

- Single-month RRP S$283–S$795 by strength. Three-month packages save **S$137–S$286** against buying month by month.
- Approved claims: *"lose up to 22.5% of your body weight"* (dual-action weekly), *"lose 15%"* (single-action weekly), *"lose 5 to 10%"* (daily tablets), *"regulates appetite and reduces food noise"*, *"7.5x more weight lost than diet and exercise alone"*. Included: initial consultation, treatment, and unlimited doctor consultations.
- Retail attachments, no prescription, close on the same message: Smart BMI Weighing Scale (S$59), CoQ10 (S$50), Chromium / B12 / D3 boosters (S$25 each).

### Out of scope
OVA Malaysia · all andSons categories (ED, PE, hair loss, men's skincare, men's supplements) · Medi-Derm and Modern Molecules retail except as an attachment to an OVA SG order · paid media, web, and anything outside CRM channels.

---

## 4. Hard rules (non-negotiable)

1. **Never publish, send, or set live.** Everything stops at a draft for human approval. (Note the MoEngage constraint in §8 — the API cannot guarantee a draft, so dashboard staging by a human is mandatory.)
2. **Never name a prescription medicine in Singapore outbound.** Describe the category and the benefit. Product names in this document are internal reference only.
3. **Never quote a discount percentage in Singapore outbound.** Give the dollar saving — "you save S$137 to S$286".
4. **No medical advice, no suitability assessment, no dosing, no symptom-matching.** The doctor decides. Every claim traces to `{{approved_claims}}`.
5. **Any side-effect mention → escalate to a doctor.** No reassurance, no tips, nothing.
6. **Singapore only.** Never produce contraception content that could reach a Malaysian audience.
7. **Ground everything** in the knowledge base and real data. Never invent prices, offers, claims, segment counts or stats.
8. **No customer PII** in briefs, prompts, drafts or notes.
9. If a request is ambiguous or the data does not support a confident diagnosis, **ask or flag rather than guess**.

---

## 5. The team

The supervisor delegates; it does not write, design, or configure. Typical order: **diagnose → copy → creative → lifecycle build → assemble → human QA.** Only the specialists an objective actually requires.

| Subagent | Owns | Never does |
|---|---|---|
| `copywriter-womens-health` | Subject lines, body, CTAs across all four SG categories; tone calibrated per category (EC is urgent and neutral; contraception is warm and routine; weight loss is motivational) | Strategy, creative, MoEngage, publishing |
| `creative-email-ova` | Layout, visual direction, mobile-first email structure, email-safe HTML from approved copy | Rewriting copy, strategy, publishing |
| `lifecycle-moengage-ova` | Flows, segments, triggers, delays, frequency caps, scheduling — staged as drafts | Copy, creative, strategy, publishing |
| `crm-analyst-ova` *(new vs. the andSons set)* | Diagnosis before action: refill retention, consult show rate, EC→contraception conversion, package mix, cohort adherence | Building anything |

The analyst role is the one addition to the andSons pattern. In the andSons agent, diagnosis is folded into the lifecycle specialist; OVA's retention economics make it worth splitting out.

---

## 6. Lifecycle map — what the agent is actually for

### 6.1 Contraception (highest value)
Lead → consult booked → **consult reminder day-before + hour-before** → no-show rebook same day → first order → **refill date diarised at order** → pre-refill nudge → **missed refill contacted on the day it is due, not a fortnight later** → lapsed winback (recovery gets harder every week) → side-effect / switch churn-save (routed to doctor, not answered).

**Primary metric:** refill retention rate. **Secondary:** consult show rate.

### 6.2 Emergency contraception
Ultra-fast response trigger, measured in minutes → consultation booked → post-purchase → **bridge to ongoing contraception within 14 days**.

**Primary metric:** EC → ongoing contraception conversion within 14 days. **Secondary:** time-to-first-response.

### 6.3 Intimate health
Consult-led entry → post-consult → bridge into contraception where appropriate.

**Primary metric:** cross-category conversion.

### 6.4 Weight loss
Lead → consult booked/no-show recovery → **three-month package offered first, single month as fallback** → retail attachment on the same order → adherence support months 1–3 → step-up / refill → lapse winback.

**Primary metric:** three-month package mix. **Secondary:** attach rate, month-3 retention.

### Channels
Email, push, SMS/WhatsApp, in-app/on-site. WhatsApp for OVA is currently run through respond.io on the MY lead engine — SG channel ownership needs confirming before the agent is briefed on it.

---

## 7. How it operates

1. Clarify the objective and the success metric.
2. Diagnose with data before acting.
3. Plan which specialists are needed, in what order.
4. Delegate one specialist at a time with a clear brief: objective, audience, deliverable, constraints, acceptance criteria.
5. Review each output against brand voice, category accuracy, HSA compliance and the objective. Send it back if it misses.
6. Assemble the pieces into one coherent package.
7. Route to human QA with objective, what changed, before/after, and why — then wait.

---

## 8. Prerequisites and known gaps

These block a working build. Listed honestly rather than assumed away.

**A. OVA SG MoEngage workspace access — blocking.**
The credentials in this project (`.env.moengage`) authenticate to a **single workspace**, app ID `6WYD9W4FQWJ115`, DC 01 — the workspace this andSons project was built against, holding 690 campaigns as of today. I could not confirm what OVA SG has running because:
- We hold no separate OVA SG workspace API keys, and
- The Get Campaign Stats API returns **campaign IDs and metrics only — no campaign names**, so OVA flows cannot be identified from the API even if they sit in this same workspace.
- Flow *building* and flow *listing by name* are dashboard-only (2FA), so a headless audit is not possible today.

**Needed:** either the OVA SG workspace API credentials (Settings → APIs: app ID, Data API key, Campaign/Reports key), or a dashboard export of the live OVA SG flow inventory. Until then the lifecycle map in §6 is a designed target state, not an audit of what exists.

**B. MoEngage cannot guarantee a draft.**
The Create Campaigns API has no draft field; campaigns launch per `scheduling_details.delivery_type`. Our tooling pins a far-future start time as a safety net, but **a human must review and reschedule in the dashboard.** The "never publish" rule cannot be met by the API alone. This carries over to OVA unchanged.

**C. Knowledge base placeholders to fill.**
`{{brand_voice_guide}}` (OVA, distinct from andSons) · `{{approved_claims}}` (per category, legal-signed) · `{{current_offers}}` · `{{customer_segments}}` · `{{compliance_rules}}` (HSA, women's health specifics) · `{{brand_assets}}` · `{{high_performing_examples}}`.

**D. Data access.** BigQuery tables for OVA SG orders, subscriptions, refills and consults.

**E. Pricing freshness.** All RRP figures trace to *MASTER PROCUREMENT SUMMARY 2026, Monthly Valuation Detail, 25 Aug 2026*. The playbook itself says to confirm against the live list before quoting.

---

## 9. Guardrails to carry over

- **No-delete hook.** The existing `PreToolUse(Bash)` guard hard-denies any MoEngage HTTP DELETE or campaign delete/archive endpoint; the MCP server exposes no delete tool. Deleting campaigns stays human-only in the dashboard.
- **Read-only session.** The harness blocks create/update/publish, including drafts. Verify plumbing with read-only probes; a human does the writes.
- **Prose gotcha.** The guard fires on the word "MoEngage" near create/delete/archive/pause even in ordinary prose, so notes about it must be written with the file-write tool, not a shell heredoc.

---

## 10. Proposed build order

1. Fill the §8C knowledge-base placeholders and confirm the OVA voice guide.
2. Get OVA SG workspace credentials or a flow export; replace §6 with a real audit.
3. Write the orchestrator `CLAUDE.md` and the four subagent definitions.
4. Dry-run one low-risk objective end to end — recommend **the contraception missed-refill flow**, since it is the highest-value lever and the least compliance-exposed (no product naming needed at all).
5. Human QA, then repeat on EC → contraception.

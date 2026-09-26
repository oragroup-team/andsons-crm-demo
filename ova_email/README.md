# OVA Email

A Slack @-mention bot, **@OvaEmail**, built on the exact same pipeline as
this repo's `backend/` andSons Email bot (`@andSons Email`): natural-language
flow requests, real multi-touchpoint flow generation (Copywriter -> Sweeper
-> Feedback loop, every touchpoint rendered as a branded image and posted to
the thread), per-step and structural revision from plain-English feedback in
the same thread, CSV/Excel attachment context, "follow this image's
template" mode, and typing **APPROVED** anywhere in the thread to export the
whole approved flow as self-contained HTML files (one combined file, plus
one file per step).

**Brand:** OVA Singapore (ORA Group's women's health telehealth brand) -
scoped from `OVA_SG_CRM_AGENT_SCOPE.md` in the project root (Thalia Bondoc,
Draft 1, 7 September 2026). Categories: contraception, emergency
contraception, intimate health, weight loss. Singapore only - see that
document's own hard rules for why (OVA Malaysia does not offer the first
three categories at all).

This is a **separate app from `backend/`** - its own folder, its own Slack
app/bot token, its own Firestore collections (`ova_`-prefixed, see
`session_store.py`) - so OVA's brand content, compliance rules, and data
never mix with andSons'. It reuses `backend/vendor/`'s third-party packages
(langchain, pydantic, anthropic, groq, flask, slack_sdk, PIL, etc.) via
`sys.path` rather than a second multi-hundred-MB install; see **Setup**
below for running it fully standalone instead.

## Brand

Built to the **OVA BRAND RESTAGE (May 2025)** guidelines:

- **Email format** = a doctor-written personal letter (brand deck, "Email
  design"): text only, long-form where the moment needs it, sent *from* a
  real verified OVA clinician (`drbenng@getova.com`, `drdonovantay@getova.com`,
  `msrddhinaidu@getova.com`). No hero photos, no CTA buttons, no icons, no
  ticks/checkboxes, no coloured blocks, no decorative design of any kind.
  The one visual is the doctor's signature plate. The call to action is a
  single inline underlined link inside a sentence. "If it's not something a
  doctor would naturally put in a personal email, it doesn't go in an OVA
  email."
- **Colours** = the real palette: bone `#F2F0E9` ground, purple `#3D1A49`
  for headlines / the signature / the one link. Max 3 colours per lockup.
- **Fonts** = the real pairing: DM Sans for headlines (title case), Space
  Grotesk for body (the default for all OVA communications). Bundled in
  `static/fonts/`.
- **Voice** = the four tone-of-voice pillars, all at once: smart not stuffy,
  calm authority, trustworthy not transactional, minimal and modern.
- **Logo** = a plain text "ova" wordmark stands in for the real refined
  logotype (a custom-drawn mark can't be reproduced in Pillow).

## Real, current gaps (carried over honestly, not smoothed over)

- **No live business data.** No OVA SG BigQuery access and no OVA SG
  MoEngage workspace API credentials exist yet (scope doc SS8A/SS8D) - an
  "insight-driven" request still gets a real draft, honestly grounded in the
  flow's own catalog data, with a plain note saying no live signal was
  checked (see `agents/feedback_node.py`'s module docstring for the exact
  seam to wire in real data once that access exists).
- **Designed target-state flows, not an audited cadence.** `flows.py`'s
  touchpoint timings/channels are the scope doc's SS6 lifecycle map turned
  into code, never independently checked against a live OVA SG MoEngage
  account - deliberately short (1-2 touchpoints) and conservative.
- **No real registered business address** - the footer shows a visible
  bracketed placeholder rather than a made-up one.
- **Doctor titles** - only Dr Ben Ng's is stated in the deck ("Chief Medical
  Officer"). Dr Donovan Tay is shown as "Doctor" and Ms R. D. Dhinaidu as
  "Pharmacist" - a reasonable read, replace in `email_image_renderer.py`'s
  `DOCTORS` dict when the real titles are confirmed.
- **Prescription medicine names** - see the note below.

## One thing to confirm with your manager / compliance

The brand deck names prescription medicines openly in some places (e.g.
"How does Ozempic work?" as an educational email headline, and in the
performance-ad examples). The CRM agent's own scope doc
(`OVA_SG_CRM_AGENT_SCOPE.md`, newer, Singapore-specific, hard rule SS4.2)
says: **never name a prescription medicine in Singapore outbound - describe
the category and the benefit**. This is a real HSA (Singapore) regulatory
matter, so this build **keeps the scope doc's rule** - no product names in
any OVA email or WhatsApp. If compliance confirms that post-purchase
educational emails to a patient already on a named treatment may reference
that specific medication (as the deck's own Ozempic example suggests), that
exception can be added per-flow - but it was not something to loosen on my
own judgment.

None of this blocks using the bot today - every draft it produces is real,
compliant copy in the OVA doctor-written voice, grounded in the scope doc's
actual approved claims and hard rules. The text-only, no-photo email is the
brand's own deliberate spec, not a gap.

## Setup

### Fastest path: reuse the andSons backend's `vendor/`

If `backend/vendor/` already exists in this repo (see the root README's
setup steps), this app finds it automatically via `sys.path` - nothing
further to install. Just:

```sh
cd ova_email
cp .env.example .env
# edit .env - see below
python3 app.py
```

### Fully standalone (no dependency on `backend/`)

```sh
cd ova_email
pip3 install -r ../backend/requirements.txt --target=vendor
cp .env.example .env
python3 app.py
```
(then remove the `backend/vendor` fallback in `app.py`'s `sys.path` setup if
you want this to never touch the other folder at all - it's additive, so
leaving it in is harmless either way.)

### Configure

Edit `.env` (see `.env.example`) - at minimum a `GROQ_API_KEY` and/or
`ANTHROPIC_API_KEY`, and the Slack credentials from the setup below.

## Using it

@-mention the bot in any channel it's been invited to, in plain English:

- `@OvaEmail write the contraception missed refill email`
- `@OvaEmail EC fast response flow`
- `@OvaEmail write something for the weight loss package offer`

Reply in the same thread (still @-mentioning the bot) to revise - "2: make
this shorter", "add a step for the retail attachment", "follow this
template" (with an image attached). Type **APPROVED** anywhere in the
thread once you're happy with it to get the whole flow as HTML files.

## Setting this up in Slack (to call it as @OvaEmail)

Same pattern as this repo's andSons `@-mention bots` (see the root
README's own Slack section) - one new Slack app, one new bot token, pointed
at this app's own endpoint.

1. **Deploy this app first** so you have a real, permanent URL - see
   **Deploying** below. (Slack's Event Subscriptions step needs a live URL
   to verify against; it can't point at your laptop.)
2. Go to `https://api.slack.com/apps` -> **Create New App** -> **From
   scratch** -> pick your workspace.
3. **Give it the name "OvaEmail"** (Settings -> Basic Information -> Display
   Information -> App name) and, if you'd like a distinct icon from the
   andSons bots, upload one there too - this is what makes `@OvaEmail`
   actually appear as that name when people @-mention it.
4. **Event Subscriptions** -> toggle on -> Request URL:
   `https://<your-ova-email-service-url>/slack/events/ova-email`
   Slack sends a `url_verification` challenge the moment you enter this -
   the endpoint already handles it, so it should verify immediately (it
   needs `SLACK_OVA_EMAIL_SIGNING_SECRET` already set on the deployed
   service first, or verification will fail).
5. Still on **Event Subscriptions** -> **Subscribe to bot events** -> add
   `app_mention`.
6. **OAuth & Permissions** -> **Scopes** -> **Bot Token Scopes** -> add
   `app_mentions:read`, `chat:write`, `files:read` (to download an attached
   CSV/Excel/image), and `files:write` (to upload the rendered images and
   the APPROVED HTML export).
7. **Basic Information** -> App Credentials -> copy the **Signing Secret**
   -> set it as `SLACK_OVA_EMAIL_SIGNING_SECRET` on your deployment.
8. **OAuth & Permissions** -> **Install to Workspace** -> Allow -> copy the
   **Bot User OAuth Token** (starts `xoxb-`) -> set it as
   `SLACK_OVA_EMAIL_BOT_TOKEN` on your deployment.
9. Restart/redeploy so the two env vars take effect, then invite the bot to
   your test channel (`/invite @OvaEmail`) and @-mention it.

## Deploying

This app is a second, independent Flask service - deploy it the same way
the root README deploys the andSons backend (a Cloud Run service built from
a `Dockerfile`), just pointed at this folder instead of `backend/`. The
simplest option: copy the root `Dockerfile` into `ova_email/`, change its
`COPY`/entrypoint paths to this folder, and deploy it as its own Cloud Run
service (e.g. `ova-email`) with its own URL - keeping it a genuinely
separate service, matching how it's a separate folder/app/Slack app
throughout. `--no-cpu-throttling` is required for the same reason documented
in the root README (Slack's webhook ack-then-background-work pattern needs
CPU allocated outside the request lifecycle).

## Project structure

```
ova_email/
  categories.py              # OVA's 4 real SG categories - voice/objections/compliance per category
  flows.py                   # OVA's real lifecycle flows (scope doc SS6), email+WhatsApp cadences
  text_sanitize.py           # shared cleanup (identical to backend/, brand-agnostic)
  file_context.py            # CSV/Excel upload summarizer (identical to backend/, brand-agnostic)
  flow_html_export.py        # APPROVED -> HTML export (identical to backend/, brand-agnostic)
  email_image_renderer.py    # branded PNG email renderer (OVA placeholder colours/footer)
  whatsapp_image_renderer.py # WABA-template PNG renderer (WhatsApp's own real app chrome)
  static/fonts/               # DM Sans + Space Grotesk (real OVA brand fonts) + Roboto (WhatsApp mock)
  session_store.py           # Firestore session storage, ova_-prefixed collections
  slack_integration.py       # Slack signature verification + Block Kit formatting
  agents/
    llm_provider.py           # per-agent Groq/Anthropic provider factory (identical to backend/)
    anthropic_agent_loop_patch.py  # Anthropic API compatibility patch (identical to backend/)
    copywriter_agent.py        # drafts OVA touchpoints - OVA voice/compliance, email + WhatsApp
    sweeper_agent.py           # OVA's real compliance checklist (brand-QA gate)
    visual_qa_agent.py         # rendered-image QA (identical mechanism to backend/)
    template_agent.py          # reads an uploaded reference image's visual style
    learned_rules_agent.py     # distills human feedback into standing rules
    head_of_crm_agent.py       # campaign brief + cadence decision + flow synthesis
    feedback_node.py           # orchestrates Copywriter -> Sweeper -> Feedback retry loop
  app.py                      # Flask app - the @OvaEmail Slack events endpoint
  .env.example
```

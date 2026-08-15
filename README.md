# andSons CRM Demo Prototype

A working prototype of two things, for manager review:

1. **AI email-generation pipeline** — Copywriter → Sweeper → Feedback-loop agents that draft
   andSons CRM emails in the approved "mixed" brand style, self-correct against a brand-QA gate,
   stop after 2 automatic retries with `needs_human_review: true` instead of looping forever, and
   support a manual "give feedback" box so a human reviewer can redirect a draft too (with full
   memory of the current draft and every prior feedback round, not just the latest note).
2. **Natural-language analytics chat** — a LangChain SQL agent that answers questions like *"what's
   our total final revenue from delivered orders in Singapore?"* against the real, live ORA BigQuery
   warehouse, with every number in its answer traced back to an actual query result and the SQL
   query shown alongside the answer.

Runs entirely locally. No venv — Python dependencies are downloaded straight into
`backend/vendor/` so the project is self-contained without touching your machine's global Python
packages.

## Brand & data grounding

The flow taxonomy, brand voice, compliance rules, product catalogue, golden P1 template, and hero
image doctrine are all sourced from andSons' real CRM knowledge base
(`CRM_Email_Generation_Data/&SONS CRM Knowledge` and `AS SG HL Knowledge v2`), not invented —
see `backend/flows.py` for the per-flow briefs, `backend/image_bank.py` for the approved hero
photos, and their source docs. Deliberately out of scope (the real production system also does
these, but they're a different-shaped app): multi-touchpoint flow sequences with WhatsApp legs,
and the extra pipeline agents (Head of CRM, Creative Director, Visual QA). This app stays
single-email-per-click, four agents (Copywriter, Sweeper, Feedback node, Analytics).

The Analytics Chat queries the real ORA BigQuery data warehouse directly (`ora_bigquery_pipeline`,
scoped to the real andSons tables) — see **Database schema** below.

## Prerequisites

- Python 3.9+ and `pip3`
- Node.js + `npm` on your `PATH` (only needed the first time `app.py` runs, to build the
  frontend — see step 3)
- A Groq and/or Anthropic API key
- A Google Cloud service account with BigQuery read access to the ORA warehouse (see
  **Analytics data source** below) — the Analytics Chat has no offline/mock fallback, it requires
  this to function

## Tech stack

- Backend: Flask (Python)
- Agent orchestration: LangChain (`langchain-groq` + `langchain-anthropic`, provider is
  per-agent and configurable via `.env`)
- Database: Google BigQuery (`google-cloud-bigquery` + `sqlalchemy-bigquery`), live and read-only
- File parsing: `pandas` + `openpyxl`, for CSV/Excel files uploaded via Slack (see **Slack
  integration** below)
- Frontend: React + Vite, plain `fetch` calls to the Flask API

## Project structure

```
backend/
  flows.py                  # canonical andSons Hair Loss lifecycle flows (real, not invented)
  text_sanitize.py           # shared cleanup: strips em-dashes/curly quotes/markdown from LLM output
  image_bank.py              # real approved hero photo bank (served from backend/static/hero_images/)
  email_image_renderer.py    # renders a drafted email as a branded PNG (for Slack), Pillow-only
  file_context.py            # summarizes an uploaded CSV/Excel file into LLM-ready context
  moengage_client.py         # real MoEngage Analytics API connector, no mock fallback
  slack_integration.py       # Slack signature verification + Block Kit formatting for slash commands
  session_store.py           # Firestore-backed session storage for the @-mention bots
  agents/
    llm_provider.py          # per-agent Groq/Anthropic provider factory
    copywriter_agent.py      # drafts the email (structured JSON), real brand voice + compliance
    sweeper_agent.py         # brand-QA pass/fail gate, real compliance checklist
    feedback_node.py         # Python functions: auto retry loop + human-feedback revision
    analytics_agent.py       # LangChain SQL agent + number-grounding check
    insight_agent.py         # investigates a business signal (BigQuery + MoEngage) into a brief
  app.py                      # Flask API
  requirements.txt
  .env.example
frontend/
  src/
    EmailPanel.jsx            # flow picker + generate + feedback box + version history
    ChatWindow.jsx             # chat UI + SQL query display
    App.jsx                    # tab switcher
  .env.example
```

## Setup

### 1. Backend — install dependencies (no venv)

```sh
cd backend
pip3 install -r requirements.txt --target=vendor
```

This downloads all Python dependencies into `backend/vendor/`. `app.py` puts that folder at the
front of `sys.path` automatically (see `_vendor_path.py`) — nothing further to activate.

### 2. Backend — configure API keys

```sh
cp .env.example .env
```

Edit `.env` and set at least one of `GROQ_API_KEY` / `ANTHROPIC_API_KEY` (whichever providers your
`*_PROVIDER` settings use — by default Copywriter uses Groq, Sweeper and Analytics use Anthropic,
since those two are the accuracy-sensitive steps). You can swap which agent uses which provider by
editing the `*_PROVIDER` values — no code changes needed:

```
COPYWRITER_PROVIDER=groq
SWEEPER_PROVIDER=anthropic
ANALYTICS_PROVIDER=anthropic
```

### 3. Run it

```sh
python3 app.py
```

That's the only thing you need to run after step 2. On first run `app.py` automatically builds the
React frontend (`npm install && npm run build` in `frontend/`) if it hasn't been built yet, then
serves both the API **and** the built frontend from one process. Open
**`http://localhost:5001`** (override the port with `FLASK_PORT` in `.env`) — the whole demo is
there, single tab, single process.

Re-running `python3 app.py` after the first time skips the build step (it already exists) and just
starts the server.

### Optional: frontend dev server (hot reload while editing the UI)

If you're iterating on the React code and want hot-reload instead of rebuilding, run the frontend
separately against the same backend:

```sh
cd frontend
npm install
npm run dev
```

Open the printed URL (typically `http://localhost:5173`) — it talks to the Flask API via CORS,
which is already enabled in `app.py`.

## The 11 flows

In Thalia's staging priority order (real andSons segments/lifecycle map, see `backend/flows.py`):

| # | Flow | Track | Reader |
|---|---|---|---|
| 1 | P1 — Plan Created, Not Purchased | Rx | Had the consult, doctor explained the plan, chose to pay later |
| 2 | P2 — Consult No-Show | Rx | Booked a free consult, didn't attend |
| 3 | P3 — OTC Cart Abandon | OTC | Added Redensyl/Trio/Kit to cart, didn't check out |
| 4 | The Valley (Month 1-3) | neutral | On treatment, no visible results yet — highest churn risk |
| 5 | Consult Booking | neutral | Finished the quiz, hasn't booked a consult |
| 6 | Replenishment / Dunning | neutral | Renewal coming up, a payment failed, or paused/skipped |
| 7 | Rx Not Suitable → OTC | OTC | Doctor ruled Rx unsuitable now |
| 8 | AOV Growth | OTC | Serum-only subscriber, 30+ days adherent |
| 9 | Win-back | neutral | Cancelled/lapsed (no results / cost / forgot) |
| 10 | Results & Milestone (Month 4+) | neutral | Seeing real change |
| 11 | Quiz Recovery | neutral | Started the assessment, didn't finish |

`track` gates the Copywriter/Sweeper: **rx** flows never mention a price or name a prescription
medicine; **otc** flows may reference the real catalogue (3% Redensyl Serum $42, Intense Hair
Growth Trio $78, Intense Hair Growth Kit $78) if it strengthens the message (optional, never
required); **neutral** flows stay price-free.

## Hero images

Every email drafted by the Copywriter also makes a hero-image judgement call: a hero is
**optional**, not a default habit — a clean text-first email is a valid, often better choice.
Selection is **bank-only** — the Copywriter picks from 18 real approved photos
(`backend/image_bank.py`), curated from the real andSons `Image_Bank` asset folder (including the
locked P1 golden-example shots and several real alternate hero exports actually used in sent P1
emails), covering a genuine range of moments: warm/affirming, quiet concern, grooming ritual,
reflective/decision, milestone/confident, and OTC product shots (single serum, full range, the
premium kit). There is no image-generation fallback; if nothing in the bank fits, the correct
choice is `none`. When the Copywriter does pick a hero, it chooses exactly one of:

- **A locked bank photo** (`smiling`, `adjusting`) — already has a headline baked into the image
  file, so the Copywriter must not add a redundant overlay headline.
- **A raw bank photo** (the other 16 keys) — no baked text, so the Copywriter writes a short,
  plain, concrete 2-5 word overlay headline for it.
- **`none`** — a deliberate text-first email; not a failure.

The actual image files are re-encoded (resized, re-compressed) copies of the source assets, served
directly by Flask at `/hero-images/<key>.jpg` (`backend/static/hero_images/`, route in
`backend/app.py`) — this works both locally and once deployed, with no external image host or API
key required.

The Sweeper checks the hero on two levels: a Python-level ground-truth check
(`sweeper_agent._hero_deterministic_issues`) confirms the image URL is a real approved bank URL —
never an invented placeholder — and that a baked hero never carries a duplicate overlay headline;
the LLM-level checklist covers judgement calls like whether the hero genuinely fits the email's
moment, and that it's a real photo, never a badge/CSS graphic. Either check failing fails the
whole email.

The frontend (`EmailPanel.jsx`) renders the hero photo (when chosen) at the top of the email card,
with the overlay headline layered on top.

## Database schema

The Analytics Chat queries the real ORA BigQuery warehouse directly — there's no local schema to
generate, the tables below are the live tables in `ora_bigquery_pipeline`, scoped to the three that
hold andSons data via `BIGQUERY_TABLES`. Every query is expected to filter
`Brand = 'AndSons' AND Country = 'Singapore'`, since these tables mix every ORA brand and country
together.

| Table | What it holds |
|---|---|
| `dotcom_plus_marketplace` | Primary order-line fact table. `status` is channel-prefixed (e.g. `[Dotcom] DELIVERED`, `[Marketplace] Completed` — match with `LIKE`, not `=`); `Prescription_Type` is "Prescription" / "Non-Prescription"; `Revenue_Type` has values like "New Customer 3 Month Sub", "Repeat 1 Month Sub"; `Revenue` (gross) vs `Final_Revenue` (net of discount + cashback); `Applicable_Discount` / `Applicable_Cashback` / `Delivery_Fee` (always 0 — andSons ships free) / `New_COGS`; `Channel` spans Dotcom/Shopee/Lazada/Zalora/TikTok. Revenue/order questions default to excluding REFUND/CANCELLED/EXPIRED statuses unless the question is explicitly about those. |
| `updated_sales_data` | Broader sales table with `Order_Type` ("Products" or "Consult Only" — a free, doctor-led consult with no product shipped) and UTM/campaign attribution: `orders_utm_medium` ("email"/"EMAIL"/"ATM_EMAIL"), `orders_utm_source` ("MoEngage", "Insider"), `orders_utm_campaign` (real flow tags like `abandoned_cart_v8`, `winback` and its ~180 winback-prefixed variants, `WelcomeFlow_New`, `tp_email`, `order_approved`). Contains PII (customer name/email) — never surfaced in a final answer, aggregates only. |
| `marketing_spend_data` | Weekly spend/clicks/impressions by channel (Facebook, Google, TikTok). `Classification` has 3 levels (Category-Level / Overall-Level / Middle-Tier) — the agent is told not to double-count when summing across them. `Category` includes HL (hair loss), Weight_Loss, Supplements, EDPE. |

There's no email send/open/click event tracking table in this warehouse — that lives in a separate
system (MoEngage), not queryable here. The Analytics agent's system prompt
(`backend/agents/analytics_agent.py`, `BIGQUERY_SCHEMA_NOTES`) spells out all of this so it reasons
about it correctly instead of guessing.

## Using the demo

- **Email Generation tab** — pick a flow, enter a first name, click Generate. The Copywriter
  drafts an email in the real Juniper voice (British spelling, no em-dashes, no exclamation marks,
  CTA follows "verb + My + noun") and makes a per-email hero-image judgement call (an approved bank
  photo, or a deliberate text-first "none" — see **Hero images** above; selection is bank-only, no
  generation fallback). The Sweeper checks it against the real andSons compliance rulebook (no Rx
  medicine names, doctor attribution, DOI-footnoted stats only, no unverified pricing, real
  WhatsApp-support footer, no cure/guarantee/urgency language, hero fits the moment and uses a real
  image). If it fails, the Feedback node re-runs the Copywriter with the full original brand
  constraints *plus* the specific correction — never the raw reasons alone — for up to 2 automatic
  retries. The "Sweeper pass/fail history" panel shows every attempt, what changed, and why it
  passed or failed. Below that, a **feedback box** lets you type your own note and regenerate — the
  revision keeps the current draft and every earlier feedback round in view, so a new note can't
  silently undo an earlier one, and old/new drafts are both shown so you can compare.
- **Analytics Chat tab** — ask a question in plain English about orders, revenue, or marketing
  spend ("how many orders has andSons had in Singapore", "what's our total final revenue from
  delivered orders in Singapore", "how much have we spent on hair loss marketing in Singapore").
  The agent queries the real BigQuery warehouse with LangChain's SQL agent, and the response shows
  the answer plus the actual SQL that was run. If a number in the drafted answer can't be traced
  back to a query result from that run, the answer is replaced with an explicit "I couldn't verify
  that figure" instead of being shown (see **Analytics data source** below). Note: there's no email
  send/open/click event tracking in this warehouse — that's tracked in a separate system (MoEngage),
  not queryable here; order-level UTM/campaign attribution is the real, grounded proxy this agent
  uses instead for "did this flow drive revenue" style questions.

## Analytics data source (live BigQuery, no mock fallback)

The Analytics Chat only ever answers from the real, live BigQuery warehouse — there is no offline
or synthetic-data fallback. `backend/agents/analytics_agent.py` requires `BIGQUERY_PROJECT_ID` to
be set and the connection to actually be usable (credentials present, IAM permission, correct
project); if it isn't, the agent raises a clear error and the chat surfaces that directly to the
user instead of silently answering from fake data.

The real warehouse (`ora_bigquery_pipeline` in the `ora-bigquery` GCP project) is the live ORA
group data warehouse — it holds every ORA brand and country together in the same tables
(`dotcom_plus_marketplace`, `updated_sales_data`, `marketing_spend_data`), not just andSons. Two
things keep this safe and scoped:
- `BIGQUERY_TABLES` (comma-separated) restricts which tables the agent can even see, via
  LangChain's `include_tables`, so it can't wander into another brand's replica database or one of
  the ~46 other staging/dated-snapshot tables in that dataset.
- The agent's system prompt hard-codes `Brand = 'AndSons' AND Country = 'Singapore'` as the default
  filter on every query against those tables, since they mix multiple brands and countries in the
  same rows.

Two guardrails always apply: a customer's individual email or personal details are never included
in a final answer (aggregates only), and no write statement is ever allowed. Both are enforced via
the prompt-level instruction and a code-level regex check (`_contains_write_operation` /
`_contains_pii` in `analytics_agent.py`) rather than a database-level guarantee, since the
provisioned service account currently has broader IAM permissions than strictly needed (see the
project's notes on requesting a scoped-down role).

## MoEngage integration (optional, for insight-driven emails)

MoEngage is where andSons' real CRM campaigns run — connecting it lets the email bot ground a draft
in real campaign/engagement data, not just BigQuery sales data. It's optional: without it,
insight-driven emails (below) still work from BigQuery alone, just without the MoEngage layer.

**What to ask for:** a **Data API key** (Data API ID + Data API Key, read-only) from the MoEngage
dashboard's **Settings → Account → APIs** page — this is separate from the write-side event-tracking
API key and doesn't need workspace admin rights to issue, the same way the Slack integration only
ever needed a non-admin app install.

MoEngage's Analytics API is dashboard/chart-based (`backend/moengage_client.py` calls the real
`GET /v5/analytics/dashboards/{id}/charts/{id}` endpoint) — there's no free-form "give me campaign
X's stats" query, only chart data for charts that already exist. Rather than maintaining a curated
allowlist, `get_all_chart_snapshots()` pulls **every chart on every workspace dashboard**
automatically (the real andSons workspace has 29 dashboards / ~138 charts) — fetched in parallel and
cached for 15 minutes, since re-hitting ~170 endpoints on every single question would be slow and
needless. Set `MOENGAGE_WORKSPACE_ID`, `MOENGAGE_DATA_API_KEY`, and `MOENGAGE_DC` (the data center
from your dashboard URL, `dashboard-0X.moengage.com` → `"0X"`) to connect.

At that scale, summarizing chart-by-chart would mean ~138 separate LLM calls per question — instead
`agents/insight_agent.py` makes ONE summarization call over every chart's real data, told which
business question it's investigating so it can pick out the charts that are actually relevant (this
workspace covers multiple andSons programs — HL, ED, PE, Weight Loss — so most charts are irrelevant
to any single question) and ignore the rest. It's explicitly instructed to describe only what's
really in the data it uses, never to invent a metric or reference a chart it didn't actually use. A
chart that fails to load (deleted, permission change) is reported and excluded, not silently dropped.

## Insight-driven emails (from a business signal, not a fixed flow)

Alongside "write the P1 email for Marcus", the email bot understands a business signal instead:
**"OTC serum sales are declining, write something to fix it for Wei"**. Instead of matching the
wording to one of the 11 flows directly, it:

1. Classifies the request as `insight` mode (`agents.copywriter_agent.parse_email_request`) and
   pulls out the underlying question ("is OTC serum revenue declining?").
2. Investigates it for real (`agents.insight_agent.investigate`) — runs the question through the
   same hardened Analytics agent used by the Analytics Chat (write-blocked, PII-blocked,
   number-verified against actual query results), plus MoEngage campaign context if configured.
3. Picks the real andSons flow whose actual trigger/audience/goal genuinely fits sending an email
   right now (`agents.copywriter_agent.pick_flow_for_signal`) — or asks a person to pick one if
   nothing genuinely fits, rather than forcing a weak match.
4. Runs the normal Copywriter → Sweeper → Feedback loop, with the investigation findings passed in
   as **internal strategy context only**: the Copywriter is explicitly instructed to use it to
   choose an angle/emphasis, never to quote a raw number, percentage, or phrase like "sales are
   down" in the customer-facing copy — the Sweeper hard-fails the email if any of that leaks
   through, on top of every existing compliance rule.

The Slack response includes a "Why this draft" line (the real, verified finding that motivated it),
and the draft still supports the same in-thread feedback loop as any other draft.

## Slack integration (optional, for office testing)

Two slash commands let coworkers use both systems directly from Slack, for testing:

- `/andsons-email <flow_slug> <first_name>` — runs the full Copywriter → Sweeper → Feedback-loop
  pipeline and posts the drafted email (including its hero image, if any) back to the channel.
- `/andsons-ask <question>` — runs the question through the Analytics agent and posts the answer,
  its verification/source status, and the SQL query back to the channel.

Both are handled by `POST /slack/generate-email` and `POST /slack/analytics` in `backend/app.py`
(formatting/verification helpers live in `backend/slack_integration.py`). Since Slack requires an
ack within 3 seconds and these agents can take several seconds, each endpoint immediately replies
with a short placeholder and does the real work in a background thread, posting the final result to
Slack's one-time `response_url` when it's ready. No bot token or OAuth install scopes are needed —
slash commands work off `response_url` alone — the only secret required is the app's **Signing
Secret**, used to verify a request genuinely came from Slack before running any agent on its behalf.

**Setup** (the app is hosted on Cloud Run at a permanent URL, so no ngrok/local tunnel is needed):

1. Go to `https://api.slack.com/apps` → **Create New App** → **From scratch** → pick your workspace.
2. **Slash Commands** → **Create New Command**, twice:
   - Command `/andsons-email`, Request URL `https://andsons-crm-demo-762730591203.us-central1.run.app/slack/generate-email`
   - Command `/andsons-ask`, Request URL `https://andsons-crm-demo-762730591203.us-central1.run.app/slack/analytics`
3. **Basic Information** → **App Credentials** → copy the **Signing Secret** → set it as the
   `SLACK_SIGNING_SECRET` environment variable on the Cloud Run service (`gcloud run services update
   andsons-crm-demo --region=us-central1 --update-env-vars=SLACK_SIGNING_SECRET=...`, or add it to
   `cloudrun-env.yaml` and redeploy via `./deploy.sh`).
4. **Install App** → **Install to Workspace** → Allow (required for slash commands to fire, even
   without any bot scopes).
5. Run either command in any channel the app has been invited to — takes effect on Cloud Run's next
   revision after the env var is set (a config-only `services update` is fast; no rebuild needed).

### @-mention bots (two separate named bots, natural language)

Instead of (or alongside) slash commands, two separate Slack apps — each with its own name and
icon — can be @-mentioned directly in a channel and talked to in plain language:

- **Email bot** — `@andSons Email <describe the email>`, e.g. `@andSons Email create the plan-not-
  purchased email for Marcus`, or a business signal instead of a named flow, e.g. `@andSons Email
  OTC serum sales are down, write something to fix it for Wei` (see **Insight-driven emails**
  above). It classifies the request, extracts the customer's first name, and — for a direct
  request — matches it against the real 11-flow catalog (`agents.copywriter_agent.parse_email_request`);
  if the first name is unclear, or a direct request doesn't map to a flow, it asks for clarification
  instead of guessing. Reply **in the same thread** with feedback (still @-mentioning the bot) and
  it revises the draft in place — feedback is tracked per Slack thread in **Firestore** (see
  **Session storage** below), so a different thread always starts a fresh draft. Attach a CSV/Excel
  file to the mention (or to a feedback
  reply) and its contents are read and folded in as extra context — real numbers from the file are
  fair game for the strategy brief, but same as any other internal context, never quoted directly
  in the customer-facing copy.
- **Analytics bot** — `@andSons Analytics <question>`, e.g. `@andSons Analytics how many orders has
  andSons had in Singapore`. The mention text is passed straight through as the question. Attach a
  CSV/Excel file and it's read and can be cited directly in the answer, combined with BigQuery when
  relevant (e.g. the file lists products, BigQuery has revenue for them).

Handled by `POST /slack/events/email` and `POST /slack/events/analytics` in `backend/app.py`,
subscribed to Slack's **Events API** (`app_mention`). Unlike slash commands, events have no
`response_url` — each app needs its own real **Bot Token** to reply via `chat.postMessage`, on top
of its own **Signing Secret**.

**Rendered email image:** the email bot posts each draft as an actual branded PNG (logo, hero
photo, body copy, a numbered "what happens next" box, CTA button, trust line, footer), not Slack
Block Kit text fragments — so it reads like a real HTML email, not a formatted message. Rendered
server-side with Pillow (`backend/email_image_renderer.py`, no headless browser — keeps the Cloud
Run image light) using the exact brand colors already defined in `frontend/src/index.css` (`--accent
#963e21` etc.), so the Slack preview and the web demo's `EmailCard` match. The bundled font
(`backend/static/fonts/Roboto-Variable.ttf`, Apache-2.0) avoids depending on whatever font happens
to be on the deploy host. The image is uploaded directly to Slack via `files_upload_v2` (needs the
`files:write` scope) rather than served from a URL this app hosts — Cloud Run instances are
stateless, so a per-request generated file saved to local disk could 404 if a later request lands
on a different instance; uploading the bytes straight to Slack sidesteps that.

**Session storage:** email feedback threads and analytics conversation history (`backend/session_store.py`)
are stored in **Firestore**, not an in-memory dict. An in-memory dict was the original implementation
and it broke in a very real way: Cloud Run replaces the running instance(s) on every deploy (wiping any
in-memory state instantly), and can run multiple concurrent instances (`maxScale=20` here) with no
guarantee two requests in the same Slack thread land on the same one — confirmed live, feedback replies
were being treated as brand-new requests with zero memory of the original draft. Firestore is shared
across every instance and every deploy, which fixes this architecturally instead of papering over it
(pinning to a single instance would still lose everything on every redeploy).

One-time setup this required, beyond application code (already done for the deployed instance, listed
here for anyone standing this up fresh):
```sh
gcloud services enable firestore.googleapis.com --project=<your-project>
gcloud firestore databases create --location=<region> --type=firestore-native --project=<your-project>
# The identity the app runs as needs read/write access - grant whichever service
# account GOOGLE_APPLICATION_CREDENTIALS resolves to (it may be a different
# project's service account, as here: BigQuery's key belongs to a different
# project than this app's own Firestore database):
gcloud projects add-iam-policy-binding <your-project> \
  --member="serviceAccount:<the service account email>" \
  --role="roles/datastore.user"
```
If that service account's own "home" project differs from the project your Firestore database lives
in, set `FIRESTORE_PROJECT_ID` explicitly (see `.env.example`) — Application Default Credentials
otherwise infers the project from the credential file itself, which pointed the client at the wrong
project entirely when this was first wired up.

**Setup**, twice (once per bot):

1. Go to `https://api.slack.com/apps` → **Create New App** → **From scratch** → pick your workspace
   → give it a distinct name (e.g. "andSons Email" / "andSons Analytics").
2. **Event Subscriptions** → toggle on → Request URL:
   - Email bot: `https://andsons-crm-demo-762730591203.us-central1.run.app/slack/events/email`
   - Analytics bot: `https://andsons-crm-demo-762730591203.us-central1.run.app/slack/events/analytics`
   Slack sends a `url_verification` challenge the moment you enter this - the endpoint already
   handles it, so the URL should verify immediately (it needs `SLACK_*_SIGNING_SECRET` already set
   on Cloud Run first, or verification will fail).
3. Still on **Event Subscriptions** → **Subscribe to bot events** → add `app_mention`.
4. **OAuth & Permissions** → **Scopes** → **Bot Token Scopes** → add `app_mentions:read`,
   `chat:write`, `files:read` (needed to download a CSV/Excel file someone attaches), and
   `files:write` (the email bot only - needed to upload the rendered email image; see
   **Rendered email image** below).
5. **Basic Information** → App Credentials → copy the **Signing Secret** → set
   `SLACK_EMAIL_SIGNING_SECRET` (or `SLACK_ANALYTICS_SIGNING_SECRET`) on Cloud Run (same
   `services update --update-env-vars` or `cloudrun-env.yaml` + `./deploy.sh` approach as above).
6. **OAuth & Permissions** → **Install to Workspace** → Allow → copy the **Bot User OAuth Token**
   (starts `xoxb-`) → set `SLACK_EMAIL_BOT_TOKEN` (or `SLACK_ANALYTICS_BOT_TOKEN`) on Cloud Run.
7. Invite the bot to your test channel (`/invite @andSons Email`), then @-mention it.

## Deploying to Google Cloud (Cloud Run)

Live at: `https://andsons-crm-demo-762730591203.us-central1.run.app`

The app runs as a single Cloud Run service, built directly from the repo's `Dockerfile` — no code
changes needed for the container itself, since `app.py` already binds to the `PORT` env var Cloud
Run injects, the same way Render's did.

**Important: the service must run with `--no-cpu-throttling`** (already in `deploy.sh`). The Slack
webhook handlers ack fast and do the real work (LLM calls, image rendering, the actual Slack post)
in a background thread afterward — Cloud Run's default (CPU allocated only while a request is being
handled) can freeze that thread mid-work on a freshly cold-started instance with no other in-flight
request keeping CPU allocated. Confirmed live: an event acked with 200, but the background thread
produced zero further log output and the Slack message never arrived. If you ever deploy without
`deploy.sh` (a raw `gcloud run deploy`/`services update`), always include `--no-cpu-throttling`.

**One-time setup:**

1. `gcloud config set project crm-mail-automation-dev` (or whichever GCP project you're deploying
   to — billing must already be enabled on it).
2. Enable the required APIs: `gcloud services enable run.googleapis.com cloudbuild.googleapis.com
   artifactregistry.googleapis.com secretmanager.googleapis.com`.
3. Upload the BigQuery service account key as a **Secret Manager** secret (never as a plain env
   var, and never committed to the repo):
   ```sh
   gcloud secrets create bigquery-service-account-key \
     --data-file=/path/to/your-service-account-key.json \
     --replication-policy=automatic
   gcloud secrets add-iam-policy-binding bigquery-service-account-key \
     --member="serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor"
   ```
   (Find `<PROJECT_NUMBER>` via `gcloud projects describe <project-id> --format="value(projectNumber)"`.
   If the service account you're using instead has direct `roles/iam.serviceAccountUser` granted on
   a BigQuery-scoped service account, you can skip the secret and pass `--service-account=...` to
   `gcloud run deploy` instead — no key file needed at all in that case.)
4. `cp cloudrun-env.example.yaml cloudrun-env.yaml` and fill in real values (Groq/Anthropic keys,
   BigQuery project/dataset/tables, Slack secrets if using the Slack integration).

**Deploy:**

```sh
./deploy.sh
```

First deploy only: copy the **Service URL** it prints, set it as `PUBLIC_BASE_URL` in
`cloudrun-env.yaml`, and run `./deploy.sh` again (or `gcloud run services update andsons-crm-demo
--region=us-central1 --update-env-vars=PUBLIC_BASE_URL=...` for a config-only update that skips
the rebuild) — required for Slack's image blocks to resolve hero image URLs.

**Continuous deployment from GitHub:** Cloud Run can rebuild and redeploy automatically on every
push to `main`, via Cloud Build → Triggers → **Connect Repository** (one-time OAuth authorization
of the Cloud Build GitHub App against `oragroup-team/andsons-crm-demo`), then **Create Trigger**
pointed at the `Dockerfile`. Until that's set up, `./deploy.sh` is the deploy path.

## Notes

- `backend/vendor/` is regenerated by the setup steps above and is git-ignored.
- `backend/text_sanitize.py` is a defensive cleanup pass (not just a prompt instruction) that strips
  em-dashes, non-breaking hyphens, curly quotes, and stray markdown from both the Copywriter's and
  the Analytics agent's output, since prompt instructions alone don't reliably stop a model from
  slipping in a "smart" character.

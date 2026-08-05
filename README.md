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
- Frontend: React + Vite, plain `fetch` calls to the Flask API

## Project structure

```
backend/
  flows.py                  # canonical andSons Hair Loss lifecycle flows (real, not invented)
  text_sanitize.py           # shared cleanup: strips em-dashes/curly quotes/markdown from LLM output
  image_bank.py              # real approved hero photo bank (served from backend/static/hero_images/)
  slack_integration.py       # Slack signature verification + Block Kit formatting for slash commands
  agents/
    llm_provider.py          # per-agent Groq/Anthropic provider factory
    copywriter_agent.py      # drafts the email (structured JSON), real brand voice + compliance
    sweeper_agent.py         # brand-QA pass/fail gate, real compliance checklist
    feedback_node.py         # Python functions: auto retry loop + human-feedback revision
    analytics_agent.py       # LangChain SQL agent + number-grounding check
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

**Setup** (the app is hosted on Render at a permanent URL, so no ngrok/local tunnel is needed):

1. Go to `https://api.slack.com/apps` → **Create New App** → **From scratch** → pick your workspace.
2. **Slash Commands** → **Create New Command**, twice:
   - Command `/andsons-email`, Request URL `https://andsons-crm-demo.onrender.com/slack/generate-email`
   - Command `/andsons-ask`, Request URL `https://andsons-crm-demo.onrender.com/slack/analytics`
3. **Basic Information** → **App Credentials** → copy the **Signing Secret** → set it as the
   `SLACK_SIGNING_SECRET` environment variable on the Render service (Environment tab).
4. **Install App** → **Install to Workspace** → Allow (required for slash commands to fire, even
   without any bot scopes).
5. Run either command in any channel the app has been invited to — no restart needed locally, since
   the change takes effect on Render's next deploy/restart after saving the env var.

### @-mention bots (two separate named bots, natural language)

Instead of (or alongside) slash commands, two separate Slack apps — each with its own name and
icon — can be @-mentioned directly in a channel and talked to in plain language:

- **Email bot** — `@andSons Email <describe the email>`, e.g. `@andSons Email create the plan-not-
  purchased email for Marcus`. It matches the request against the real 11-flow catalog and extracts
  the customer's first name with a small LLM call (`agents.copywriter_agent.parse_email_request`);
  if either is unclear, it asks for clarification instead of guessing. Reply **in the same thread**
  with feedback (still @-mentioning the bot) and it revises the draft in place — feedback is tracked
  per Slack thread, in-memory, so a different thread always starts a fresh draft.
- **Analytics bot** — `@andSons Analytics <question>`, e.g. `@andSons Analytics how many orders has
  andSons had in Singapore`. The mention text is passed straight through as the question.

Handled by `POST /slack/events/email` and `POST /slack/events/analytics` in `backend/app.py`,
subscribed to Slack's **Events API** (`app_mention`). Unlike slash commands, events have no
`response_url` — each app needs its own real **Bot Token** to reply via `chat.postMessage`, on top
of its own **Signing Secret**.

**Setup**, twice (once per bot):

1. Go to `https://api.slack.com/apps` → **Create New App** → **From scratch** → pick your workspace
   → give it a distinct name (e.g. "andSons Email" / "andSons Analytics").
2. **Event Subscriptions** → toggle on → Request URL:
   - Email bot: `https://andsons-crm-demo.onrender.com/slack/events/email`
   - Analytics bot: `https://andsons-crm-demo.onrender.com/slack/events/analytics`
   Slack sends a `url_verification` challenge the moment you enter this - the endpoint already
   handles it, so the URL should verify immediately (it needs `SLACK_*_SIGNING_SECRET` already set
   on Render first, or verification will fail).
3. Still on **Event Subscriptions** → **Subscribe to bot events** → add `app_mention`.
4. **OAuth & Permissions** → **Scopes** → **Bot Token Scopes** → add `app_mentions:read` and
   `chat:write`.
5. **Basic Information** → App Credentials → copy the **Signing Secret** → set
   `SLACK_EMAIL_SIGNING_SECRET` (or `SLACK_ANALYTICS_SIGNING_SECRET`) on Render.
6. **OAuth & Permissions** → **Install to Workspace** → Allow → copy the **Bot User OAuth Token**
   (starts `xoxb-`) → set `SLACK_EMAIL_BOT_TOKEN` (or `SLACK_ANALYTICS_BOT_TOKEN`) on Render.
7. Invite the bot to your test channel (`/invite @andSons Email`), then @-mention it.

## Notes

- `backend/vendor/` is regenerated by the setup steps above and is git-ignored.
- `backend/text_sanitize.py` is a defensive cleanup pass (not just a prompt instruction) that strips
  em-dashes, non-breaking hyphens, curly quotes, and stray markdown from both the Copywriter's and
  the Analytics agent's output, since prompt instructions alone don't reliably stop a model from
  slipping in a "smart" character.

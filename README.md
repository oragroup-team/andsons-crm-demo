# andSons CRM Demo Prototype

A working prototype of two things, for manager review:

1. **AI email-generation pipeline** — Copywriter → Sweeper → Feedback-loop agents that draft
   andSons CRM emails in the approved "mixed" brand style, self-correct against a brand-QA gate,
   stop after 2 automatic retries with `needs_human_review: true` instead of looping forever, and
   support a manual "give feedback" box so a human reviewer can redirect a draft too (with full
   memory of the current draft and every prior feedback round, not just the latest note).
2. **Natural-language analytics chat** — a LangChain SQL agent over a synthetic andSons CRM
   database that answers questions like *"how many products were sold after the P1 email was
   clicked?"*, with every number in its answer traced back to an actual query result and the SQL
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

The `orders` and `marketing_spend` mock tables are likewise grounded in real ORA BigQuery table
metadata (`BigQuery Metadata.xlsx`) rather than invented columns — see **Database schema** below.
That workbook documents three real production tables spanning all 5 ORA brands, 3 countries, and
4 sales channels (Dotcom + Lazada/Shopee/TikTok); this demo deliberately imports only the
subset of columns/semantics relevant to a single-brand, single-market (andSons, Singapore),
Dotcom-only analytics demo — not the full multi-brand/multi-country/marketplace schema.

## Prerequisites

- Python 3.9+ and `pip3`
- Node.js + `npm` on your `PATH` (only needed the first time `app.py` runs, to build the
  frontend — see step 3)
- A Groq and/or Anthropic API key
- Optional: an OpenAI API key, only needed for the hero-image "generate" fallback (see
  **Hero images** below) — without it, that path is simply skipped

## Tech stack

- Backend: Flask (Python)
- Agent orchestration: LangChain (`langchain-groq` + `langchain-anthropic`, provider is
  per-agent and configurable via `.env`)
- Database: SQLite, populated by `backend/seed_data.py`
- Frontend: React + Vite, plain `fetch` calls to the Flask API

## Project structure

```
backend/
  flows.py                  # canonical andSons Hair Loss lifecycle flows (real, not invented)
  text_sanitize.py           # shared cleanup: strips em-dashes/curly quotes/markdown from LLM output
  image_bank.py              # real approved hero photo URLs + the Gen-AI image prompt standard
  image_gen.py               # OpenAI gpt-image-1 wrapper for the "generate" hero fallback
  slack_integration.py       # Slack signature verification + Block Kit formatting for slash commands
  agents/
    llm_provider.py          # per-agent Groq/Anthropic provider factory
    copywriter_agent.py      # drafts the email (structured JSON), real brand voice + compliance
    sweeper_agent.py         # brand-QA pass/fail gate, real compliance checklist
    feedback_node.py         # Python functions: auto retry loop + human-feedback revision
    analytics_agent.py       # LangChain SQL agent + number-grounding check
  synthetic_templates/
    golden_examples.json     # 1 approved + 5 flawed emails, for testing the Sweeper's judgment
  seed_data.py                # generates the SQLite DB + golden_examples.json
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

This downloads all Python dependencies into `backend/vendor/`. Every backend entry-point script
(`seed_data.py`, `app.py`) puts that folder at the front of `sys.path` automatically (see
`_vendor_path.py`) — nothing further to activate.

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

That's the only thing you need to run after step 2. On first run `app.py` automatically:

- seeds `backend/andsons.db` if it doesn't exist yet (240 customers, 520 emails across the 11 real
  andSons Hair Loss flows, ~40% open rate, ~15% click rate, orders for roughly half of clicked
  emails), and writes `backend/synthetic_templates/golden_examples.json`;
- builds the React frontend (`npm install && npm run build` in `frontend/`) if it hasn't been
  built yet.

Then it serves both the API **and** the built frontend from one process. Open
**`http://localhost:5001`** (override the port with `FLASK_PORT` in `.env`) — the whole demo is
there, single tab, single process.

Re-running `python3 app.py` after the first time skips both steps (DB and build already exist)
and just starts the server.

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

Every email drafted by the Copywriter also makes a hero-image judgement call, matching the real
production doctrine (`backend/image_bank.py`, `Agent Prompts VERBATIM`): a hero is **optional**,
not a default habit — a clean text-first email is a valid, often better choice. When the
Copywriter does pick a hero, it chooses exactly one of:

- **A locked bank photo** (`smiling`, `adjusting`) — already has a headline baked into the image
  file, so the Copywriter must not add a redundant overlay headline.
- **A raw bank photo** (`earlysigns`, `combing`, `thoughtful`, `redensyl`) — no baked text, so the
  Copywriter writes a short, plain, concrete 2-5 word overlay headline for it.
- **`generate`** — only when none of the 6 approved photos genuinely fit this email's moment. The
  Copywriter writes one specific pose/scene line, which is combined with the approved andSons
  Gen-AI image prompt standard (`image_bank.IMAGE_STANDARD_PROMPT`) and sent to OpenAI's
  `gpt-image-1` (`backend/image_gen.py`), capped at one generation per email. If `OPENAI_API_KEY`
  isn't set, or the call fails for any reason, generation is skipped and the email falls back to a
  text-first layout — it never blocks or errors out the pipeline.
- **`none`** — a deliberate text-first email; not a failure.

The Sweeper checks the hero on two levels: a Python-level ground-truth check
(`sweeper_agent._hero_deterministic_issues`) confirms the image URL is either a real approved bank
URL or a real generated image payload — never an invented placeholder — and that a baked hero
never carries a duplicate overlay headline; the LLM-level checklist covers judgement calls like
whether the hero genuinely fits the email's moment, and that it's a real photo, never a badge/CSS
graphic. Either check failing fails the whole email.

The frontend (`EmailPanel.jsx`) renders the hero photo (when chosen) at the top of the email card,
with the overlay headline layered on top and a small caption noting whether it came from the image
bank or was AI-generated.

## Database schema

`backend/seed_data.py` generates six tables. `customers`, `campaigns`, `emails_sent`, and
`email_events` are demo-specific (not from the BigQuery workbook). `products`, `orders`, and
`marketing_spend` carry real column names/semantics from `BigQuery Metadata.xlsx`:

| Table | Real-world grounding |
|---|---|
| `products.prescription_type` | `dotcom_plus_marketplace.Prescription_Type` — "Prescription" / "Non-Prescription" |
| `orders.status` | `dotcom_plus_marketplace.status` (Dotcom values) — DELIVERED, PACKED_DISPATCHED, PAID_APPROVED, PAID_PENDING_DOCTOR, PAID_CONSULTATION_ONLY, REFUND, CANCELLED |
| `orders.order_type` | `updated_sales_data.Order_Type` — "Products" or "Consult Only" (a free, doctor-led consult with no product shipped) |
| `orders.revenue_type` | `dotcom_plus_marketplace.Revenue_Type` (simplified) — e.g. "New Customer 3 Month Sub", "Repeat 1 Month Sub" |
| `orders.revenue` / `final_revenue` | `Revenue` (gross) / `Final_Revenue` (net of discount + cashback) |
| `orders.discount_amount` / `cashback_amount` / `delivery_fee` / `cogs` | `Applicable_Discount` / `Applicable_Cashback` / `Delivery_Fee` (always 0 — andSons ships free) / `New_COGS` |
| `marketing_spend` | `marketing_spend_data` — weekly spend/clicks/impressions by channel (Facebook, Google, TikTok), with `classification` ("Overall-Level" vs "Category-Level") so the agent knows not to double-count when summing |

`REFUND` orders keep their original `revenue`/`cogs` but `final_revenue` is 0 (paid, then fully
reversed); `CANCELLED` orders zero out everything (never captured). The Analytics agent's system
prompt (`backend/agents/analytics_agent.py`) spells out these semantics so it reasons about them
correctly instead of guessing.

## Using the demo

- **Email Generation tab** — pick a flow, enter a first name, click Generate. The Copywriter
  drafts an email in the real Juniper voice (British spelling, no em-dashes, no exclamation marks,
  CTA follows "verb + My + noun") and makes a per-email hero-image judgement call (an approved bank
  photo, an AI-generated fallback, or a deliberate text-first "none" — see **Hero images** above).
  The Sweeper checks it against the real andSons compliance rulebook (no Rx medicine names, doctor
  attribution, DOI-footnoted stats only, no unverified pricing, real WhatsApp-support footer, no
  cure/guarantee/urgency language, hero fits the moment and uses a real image). If it fails, the
  Feedback node re-runs the Copywriter with the full original brand constraints *plus* the
  specific correction — never the raw reasons alone — for up to 2 automatic retries. The "Sweeper
  pass/fail history" panel shows every attempt, what changed, and why it passed or failed.
  Below that, a **feedback box** lets you type your own note and regenerate — the revision keeps
  the current draft and every earlier feedback round in view, so a new note can't silently undo an
  earlier one, and old/new drafts are both shown so you can compare.
- **Analytics Chat tab** — ask a question in plain English, from simple email metrics ("how many
  products were sold after the P1 email was clicked") to finance and marketing questions ("what's
  our total final revenue from delivered orders", "how much did we spend on Google marketing for
  Hair Loss last month", "how many orders were refunded"). The agent queries the SQLite database
  with LangChain's SQL agent, and the response shows the answer plus the actual SQL that was run.
  If a number in the drafted answer can't be traced back to a query result from that run, the
  answer is replaced with an explicit "I couldn't verify that figure" instead of being shown.
  A small "Source: mock data" / "Source: live BigQuery" line shows which database actually
  answered (see **Analytics data source** below).

## Analytics data source (mock data, with a live BigQuery fallback path)

By default the Analytics Chat answers from the local SQLite mock database. If `BIGQUERY_PROJECT_ID`
is set in `.env`, `backend/agents/analytics_agent.py` tries a real, read-only BigQuery connection
first and only falls back to the mock database if that connection isn't actually usable right now
(missing credentials, no IAM permission, wrong project) — nothing else to configure for the
fallback itself. Every response reports which one answered via `data_source` (`"bigquery"` or
`"mock"`). Two guardrails apply regardless of which database is answering: the connection is opened
read-only so no query can ever write, and a customer's individual email/personal details are never
included in a final answer (aggregates only) — both enforced at the prompt level and independently
double-checked in code (`_contains_write_operation` / `_contains_pii` in `analytics_agent.py`).

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

**Setup:**

1. Get a public HTTPS URL to your locally-running `app.py` (Slack can't reach `localhost`) — e.g.
   `ngrok http 5001` — and note the `https://...ngrok...` forwarding URL it prints. Free ngrok URLs
   change every restart, so you'll need to update the Slack command URLs again if you restart it.
2. Go to `https://api.slack.com/apps` → **Create New App** → **From scratch** → pick your workspace.
3. **Slash Commands** → **Create New Command**, twice:
   - Command `/andsons-email`, Request URL `https://<your-ngrok-url>/slack/generate-email`
   - Command `/andsons-ask`, Request URL `https://<your-ngrok-url>/slack/analytics`
4. **Basic Information** → **App Credentials** → copy the **Signing Secret** → set
   `SLACK_SIGNING_SECRET` in `backend/.env` to it.
5. **Install App** → **Install to Workspace** → Allow (required for slash commands to fire, even
   without any bot scopes).
6. Restart `python3 app.py` so it picks up the new env var, then run either command in any channel
   the app has been invited to.

## Notes

- `backend/vendor/` and `backend/andsons.db` are regenerated by the setup steps above and are
  git-ignored.
- The Sweeper's judgment can be spot-checked against `backend/synthetic_templates/golden_examples.json`
  (1 approved P1 email, 5 deliberately flawed variations with reasons — including a named-Rx-medicine
  violation) by calling `agents.sweeper_agent.sweep_email()` on each `email_text` — it should pass
  the approved one and fail all 5 flawed ones.
- `backend/text_sanitize.py` is a defensive cleanup pass (not just a prompt instruction) that strips
  em-dashes, non-breaking hyphens, curly quotes, and stray markdown from both the Copywriter's and
  the Analytics agent's output, since prompt instructions alone don't reliably stop a model from
  slipping in a "smart" character.

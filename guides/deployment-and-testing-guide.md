# Deployment and Testing Guide

This covers how to deploy, redeploy, and test all three agents:
**@andSons Email**, **@andSons Analytics**, and **@OvaEmail**.

There are only **two actual services** to deploy, not three — andSons Email and andSons Analytics
are two Slack bots sharing one backend (`backend/`), while OVA Email is a completely separate app
(`ova_email/`):

| Cloud Run service | Folder | Deploy script | Slack bots it serves |
|---|---|---|---|
| `andsons-crm-demo` | `backend/` (deployed from repo root) | `./deploy.sh` (root) | @andSons Email, @andSons Analytics |
| `ova-email` | `ova_email/` | `ova_email/deploy.sh` | @OvaEmail |

Both are plain Flask apps on Google Cloud Run, built straight from a `Dockerfile` — no separate
build pipeline to maintain.

## 1. Prerequisites

- `gcloud` CLI installed and authenticated: `gcloud auth login`, then
  `gcloud config set project crm-mail-automation-dev` (or whichever GCP project is in use).
- Billing enabled on that GCP project.
- Access to the project's Secret Manager secrets and Firestore database (ask whoever manages GCP
  IAM for this project to grant you the right role if a `gcloud` command below fails with a
  permissions error).

You do **not** need your own Groq/Anthropic API keys to deploy or test the existing services — the
real keys already live in `cloudrun-env.yaml` (gitignored, already filled in) for each app. You'd
only need your own keys if setting up a brand-new environment from scratch.

## 2. Deploying a code change

**andSons Email + andSons Analytics** (same service, one deploy):
```sh
cd CRM_Demo_Prototype        # repo root
./deploy.sh
```

**OVA Email**:
```sh
cd CRM_Demo_Prototype/ova_email
./deploy.sh
```

Each script rebuilds the container from source and creates a new Cloud Run revision, which starts
serving 100% of traffic within a minute or two once it's up. Both scripts print a **Service URL**
and a **revision name** (e.g. `ova-email-00013-kql`) when done — worth noting down if you need to
check logs for that specific deploy afterward.

**Important — do not remove `--no-cpu-throttling` from either deploy script.** Both bots ack
Slack's webhook immediately and do the real work (calling the AI models, rendering images, posting
the final reply) in a background thread afterward. Without this flag, Cloud Run can freeze that
background thread on a freshly-started instance, and the Slack reply never arrives — with no error
anywhere. This has happened for real; see **Known issue: silent non-response** below.

If you only need to change an environment variable (not the code itself), you can skip the full
rebuild:
```sh
gcloud run services update andsons-crm-demo --region=us-central1 --update-env-vars=KEY=value
gcloud run services update ova-email --region=us-central1 --update-env-vars=KEY=value
```

### Config files you might need to touch

| File | What it's for |
|---|---|
| `cloudrun-env.yaml` (root) | Real env vars + API keys for andSons Email/Analytics. Gitignored — never commit it. |
| `ova_email/cloudrun-env.yaml` | Same, for OVA Email. |
| `cloudrun-env.example.yaml` / `ova_email/.env.example` / `backend/.env.example` | Git-tracked templates showing what a fresh setup needs — keep these in sync if you add a new required env var. |

Slack credentials (signing secret + bot token, one pair per bot) are **not** in these YAML files —
they live in Google Secret Manager and are attached at deploy time via `--set-secrets` in each
`deploy.sh`. To rotate one, update it in Secret Manager (`gcloud secrets versions add
<secret-name> --data-file=...`) and redeploy — no code change needed.

## 3. Testing a change

### 3a. Quick sanity check — health endpoint

Both services expose `/health`:
```sh
curl https://andsons-crm-demo-762730591203.us-central1.run.app/health
curl https://ova-email-762730591203.us-central1.run.app/health
```
A `{"status": "ok", ...}` response just means the process started — it does **not** confirm the AI
agents themselves work (they need real API keys and can fail independently of the web server).

### 3b. Real testing — through Slack

This is the actual test that matters, since these bots only really work end-to-end through Slack.
After deploying:

1. Go to the Slack channel each bot is in (or invite it: `/invite @OvaEmail`).
2. Send a real test request, e.g. `@OvaEmail write the contraception missed refill email`.
3. Confirm you get a reply — a rendered email image within roughly 10–60 seconds for a single
   email, up to a few minutes for a multi-step flow.
4. Reply in the same thread with a feedback request (e.g. "make this shorter") and confirm the bot
   revises rather than starting over or ignoring the thread.
5. For @andSons Analytics: ask a real business question and confirm the SQL query shown alongside
   the answer looks sane (filters on the right brand/country, etc.) — a data-literate teammate
   should spot-check this occasionally, not just trust the prose answer.

There is no automated test suite for these agents — testing is manual, through Slack, because the
actual product is the quality of an AI-generated draft, which isn't something a unit test can
meaningfully assert on. When you change a prompt or a rule, the right test is: **send 2–3 real
requests that exercise the changed behavior, and read the actual output.**

### 3c. Checking what actually happened — Cloud Run logs

If something looks wrong (a bad draft, no response, an error), the logs are the source of truth.
Both agents log every LLM call, every Sweeper pass/fail with its stated reasons, and every retry:

```sh
gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=ova-email' \
  --project=crm-mail-automation-dev --freshness=15m --limit=200 \
  --format="value(timestamp,textPayload)"
```
(swap `ova-email` for `andsons-crm-demo` for the other service). Narrow with `--freshness=1h`,
`--freshness=1d`, etc., or add a `timestamp>="..."` filter to look at a specific window.

What to look for:
- `INFO:feedback_node:Flow ... touchpoint N ... attempt N: pass=False severity=... reasons=[...]` —
  the Sweeper rejected a draft and why. A few of these per flow is normal (the system
  self-corrects); many in a row on the same touchpoint suggests the request is hitting a real
  content conflict worth reading the reasons for.
- `INFO:head_of_crm_agent:Synthesized new flow ...` — the request didn't match one of the real
  pre-built flows, so a new one was designed on the fly. The synthesized flow is saved and reused
  if the same kind of request comes in again.
- A sudden stop in log lines with no error, no final Slack post — see **Known issue** below.

### 3d. Known issue: silent non-response on long-running requests

**Symptom**: you send a request (usually a multi-step flow with several touchpoints), the logs
show real progress for a while, and then everything just stops — no error, no Slack reply, ever.

**Cause**: this Cloud Run service scales down to zero instances when idle (`--min-instances=0`,
to keep cost near-zero). Slack requires a reply within 3 seconds, so the app acks immediately and
does the real work in a background thread. If Cloud Run decides to scale the instance down while
that background thread is still running (it only tracks active HTTP requests, not background
threads), the thread gets killed mid-work with no chance to log an error or post a result.

This is more likely on a request that takes unusually long — a 5-step flow with several Sweeper
rejections in a row can run past 2-3 minutes, well into the danger zone.

**What to do about it**: if you hit this, the request simply needs to be re-sent — there's no
partial state to recover. If it happens often enough to be a real problem, the fix is to set
`--min-instances=1` on the affected service (keeps one instance always warm, at a small recurring
cost — roughly a few dollars/month at this size) rather than working around it repeatedly. This is
a deliberate cost/reliability tradeoff, not an oversight — raise it with whoever owns the GCP
billing for this project before changing it.

## 4. Rolling back

If a deploy introduces a real regression, Cloud Run keeps every previous revision:
```sh
gcloud run revisions list --service=ova-email --region=us-central1
gcloud run services update-traffic ova-email --region=us-central1 --to-revisions=<previous-revision>=100
```
This instantly routes all traffic back to the working revision without rebuilding anything — use
this first if something's badly broken, then fix and redeploy properly afterward.

## 5. Local development (optional)

Both apps can run locally for faster iteration than a full Cloud Run deploy, though the actual
Slack testing loop still needs a real, reachable URL (Slack can't call `localhost`) — a tool like
`ngrok` can bridge that gap temporarily if you need to test Slack behavior against local code.

```sh
# andSons backend
cd backend && pip3 install -r requirements.txt --target=vendor
python3 app.py

# OVA Email
cd ova_email && pip3 install -r requirements.txt
python3 app.py
```
Both need their respective `.env` (copy from `.env.example` and fill in real values — the same
keys already in the deployed `cloudrun-env.yaml` files work fine locally too).

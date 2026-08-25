#!/bin/bash
# Deploys this app to Cloud Run — the Google Cloud equivalent of the old
# render.yaml Blueprint (Render auto-created the service from that file;
# this script does the same job for Cloud Run via `gcloud run deploy`).
#
# One-time setup before running this:
#   1. cp cloudrun-env.example.yaml cloudrun-env.yaml, fill in real values.
#   2. Upload the BigQuery service account key as a Secret Manager secret
#      (see README "Deploying to Google Cloud" for the exact commands) —
#      this avoids putting the key's JSON content in a plain env var.
#   3. gcloud config set project <your-project>
#
# Usage: ./deploy.sh
set -euo pipefail

SERVICE_NAME="andsons-crm-demo"
REGION="us-central1"
SECRET_NAME="bigquery-service-account-key"

# The 4 Slack credentials live in Secret Manager, not cloudrun-env.yaml -
# real incident this fixes: --env-vars-file (below) REPLACES the entire
# environment on every deploy rather than merging into it, and an older/
# incomplete local copy of cloudrun-env.yaml silently wiped these out from
# the live service, taking both Slack bots down with zero visible error
# until someone actually tried messaging them. --set-secrets is a separate
# mechanism from --env-vars-file, so these 4 survive regardless of what
# cloudrun-env.yaml does or doesn't contain. Secret names created via
# `gcloud secrets create` - see README if any need rotating.
SLACK_SECRETS="SLACK_EMAIL_SIGNING_SECRET=slack-email-signing-secret:latest,SLACK_EMAIL_BOT_TOKEN=slack-email-bot-token:latest,SLACK_ANALYTICS_SIGNING_SECRET=slack-analytics-signing-secret:latest,SLACK_ANALYTICS_BOT_TOKEN=slack-analytics-bot-token:latest"

# Same durable-storage reasoning as the Slack secrets above - the MoEngage
# Campaigns Search API key (Settings -> Account -> APIs -> "Campaign
# report/Business events/..." tile, NOT the same key as MOENGAGE_DATA_API_KEY,
# which only works for the Analytics Dashboards API).
MOENGAGE_SECRETS="MOENGAGE_CAMPAIGN_API_KEY=moengage-campaign-api-key:latest"

# --no-cpu-throttling matters here specifically: the Slack webhook handlers
# ack fast and do the real work (LLM calls, image rendering, the Slack post
# itself) in a background thread AFTER the HTTP response is sent. Cloud
# Run's default (CPU allocated only during request handling) can freeze
# that background thread mid-work on a freshly cold-started instance with
# no other in-flight requests keeping CPU allocated - confirmed live: one
# event acked fine but the background thread produced zero further log
# output and the Slack post never happened. Always-allocated CPU fixes it.
#
# --min-instances=1 fixes a real, DIFFERENT layer of the same class of bug,
# found live 2026-08-24: the Slack webhook's own HTTP request returns
# almost instantly (it acks, spawns a daemon background thread, done) - so
# from Cloud Run's own instance-scaling view, that instance has ZERO active
# requests the moment the ack returns, even while the daemon thread is
# still genuinely working (a multi-iteration SQL agent question can run for
# several real minutes). With no minimum instance count, Cloud Run's
# default autoscaling can recycle an "idle" instance mid-work - the daemon
# thread dies with no exception, no log line, and no Slack message ever
# posted. --no-cpu-throttling alone does not prevent this: it only keeps
# CPU allocated WHILE an instance is alive, it does not stop Cloud Run from
# deciding to kill the instance entirely. Keeping one instance always warm
# closes that gap. Real cost tradeoff, accepted deliberately: this keeps
# one instance running 24/7 rather than scaling to zero - worth it for a
# tool real teammates are relying on for real answers in Slack.
#
# --timeout raised from 120 to 300: confirmed live, a genuinely complex
# multi-table/multi-brand question can take longer than 120s end to end on
# the direct /ask endpoint (which blocks synchronously, unlike the Slack
# path above) - a real "upstream request timeout" was reproduced live on
# one such question. 300s is generous headroom without being unbounded.
#
# NOTE: this exact block has been found reverted on disk twice now by
# something outside this session's own edits (not a deploy - the live
# Cloud Run service itself has kept these settings the whole time,
# confirmed independently via `gcloud run services describe`; only this
# local file's content reverted). Restored again here before this deploy -
# if it happens a third time, this comment is the paper trail for why.

if [ ! -f cloudrun-env.yaml ]; then
  echo "cloudrun-env.yaml not found — copy cloudrun-env.example.yaml and fill it in first."
  exit 1
fi

gcloud run deploy "$SERVICE_NAME" \
  --source=. \
  --region="$REGION" \
  --allow-unauthenticated \
  --env-vars-file=cloudrun-env.yaml \
  --set-secrets="/secrets/gcp-key.json=${SECRET_NAME}:latest,${SLACK_SECRETS},${MOENGAGE_SECRETS}" \
  --memory=1Gi \
  --timeout=300 \
  --min-instances=1 \
  --no-cpu-throttling

echo ""
echo "Deployed. If this was the first-ever deploy, grab the Service URL"
echo "printed above, put it in cloudrun-env.yaml's PUBLIC_BASE_URL, and"
echo "run this script again (or use 'gcloud run services update' for a"
echo "config-only change that skips the rebuild)."

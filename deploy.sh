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
  --timeout=120 \
  --no-cpu-throttling

echo ""
echo "Deployed. If this was the first-ever deploy, grab the Service URL"
echo "printed above, put it in cloudrun-env.yaml's PUBLIC_BASE_URL, and"
echo "run this script again (or use 'gcloud run services update' for a"
echo "config-only change that skips the rebuild)."

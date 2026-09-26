#!/bin/bash
# Deploys ova-email to Cloud Run. Mirrors the root deploy.sh's own
# reasoning for the andSons service - see that file's comments for the
# full incident history behind --no-cpu-throttling and the secrets-vs-env-
# vars-file split.
#
# --env-vars-file (below) REPLACES the entire environment on every deploy,
# same real gap documented in the root deploy.sh - so the two Slack
# credentials live in Secret Manager via --set-secrets instead, which is a
# separate mechanism that survives regardless of what cloudrun-env.yaml
# does or doesn't contain.
#
# One-time setup before running this:
#   1. cp cloudrun-env.example.yaml cloudrun-env.yaml (if you don't already
#      have one), fill in real values (see README.md).
#   2. gcloud config set project crm-mail-automation-dev (or your project)
#
# Usage: ./deploy.sh
set -euo pipefail

SERVICE_NAME="ova-email"
REGION="us-central1"
BIGQUERY_SECRET="bigquery-service-account-key"  # shared with the andSons backend - same Firestore project, same access

SLACK_SECRETS="SLACK_OVA_EMAIL_SIGNING_SECRET=slack-ova-email-signing-secret:latest,SLACK_OVA_EMAIL_BOT_TOKEN=slack-ova-email-bot-token:latest"

if [ ! -f cloudrun-env.yaml ]; then
  echo "cloudrun-env.yaml not found - copy cloudrun-env.example.yaml and fill it in first."
  exit 1
fi

gcloud run deploy "$SERVICE_NAME" \
  --source=. \
  --region="$REGION" \
  --allow-unauthenticated \
  --env-vars-file=cloudrun-env.yaml \
  --set-secrets="/secrets/gcp-key.json=${BIGQUERY_SECRET}:latest,${SLACK_SECRETS}" \
  --memory=1Gi \
  --timeout=300 \
  --min-instances=0 \
  --no-cpu-throttling

echo ""
echo "Deployed. Once this bot is live and getting real Slack traffic, bump"
echo "--min-instances to 1 (see the root deploy.sh's comment for why - the"
echo "andSons bot hit a real bug where an autoscaled-to-zero instance got"
echo "recycled mid-background-work)."

# --- Publish to GitHub (only reached if the deploy above succeeded) --------
# Keeps the repo identical to what is live, whoever runs the deploy, so
# teammates can just `git pull`. Optional commit message: ./deploy.sh "what changed".
# Skip with SKIP_GIT_PUSH=1, preview with DRY_RUN=1. See ../publish_to_github.sh.
"$(git rev-parse --show-toplevel)/publish_to_github.sh" "${1:-Deploy ova-email ($(date -u '+%Y-%m-%d %H:%M') UTC)}" \
  ova_email guides publish_to_github.sh .gitignore

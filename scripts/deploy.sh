#!/bin/bash
# Deploy the app to Cloud Run.
#
# This replaces the old App Engine deployment (`gcloud app deploy` of
# app.yaml). Everything app.yaml used to declare lives here as flags, so a
# redeploy always converges the service to what this script says.
#
# One-time project setup (APIs, service account, IAP) is documented in
# doc/deploy.md; IAP enablement is not touched here and survives redeploys.

set -euo pipefail

# Where the app is deployed. Keep in sync with SERVICE/REGION in
# scripts/iap.py.
SERVICE="machines"
REGION="us-central1"

PROJECT_ID="$(gcloud config get-value project)"

# The identity the deployed app runs as (created by scripts/service_account.py).
SERVICE_ACCOUNT="machines-app-sa@${PROJECT_ID}.iam.gserviceaccount.com"

# Admins see and control every machine; students see only the machine whose
# owner label maps to their email in Datastore (pushed by scripts/iap.py sync).
#
# Optional shared secret protecting the app before IAP is enabled: append
# ",ACCESS_TOKEN=<long random string>" and hand students the link
#   https://<service-url>/?token=<the value>
# Once IAP is on the token is redundant and should stay unset.
ENV_VARS="ADMIN_EMAILS=mark.veltzer@gmail.com"

exec gcloud run deploy "${SERVICE}" \
	--source . \
	--region "${REGION}" \
	--no-allow-unauthenticated \
	--service-account "${SERVICE_ACCOUNT}" \
	--max-instances 2 \
	--set-env-vars "${ENV_VARS}"

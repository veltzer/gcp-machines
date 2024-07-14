#!/bin/bash -e

PROJECT_ID="veltzer-machines-id"
SERVICE_ACCOUNT_NAME="gae-machines-engine-sa"
SERVICE_ACCOUNT_NAME="gae-machines-engine-sa@veltzer-machines-id.iam.gserviceaccount.com"

# Get Service Account Email
SERVICE_ACCOUNT_EMAIL=$(gcloud iam service-accounts describe "$SERVICE_ACCOUNT_NAME" --format="value(email)")

# Fetch IAM Policy
POLICY_JSON=$(gcloud projects get-iam-policy "$PROJECT_ID")
echo "${POLICY_JSON}"
exit 1

# Check for 'gmail.send' in Relevant Roles
HAS_PERMISSION=$(echo "$POLICY_JSON" | jq -r '.bindings[] |
  select((.role == "roles/gmail.send") or (.role == "roles/owner") or (.role == "roles/editor")) |
  .members[] |
  select(contains("'$SERVICE_ACCOUNT_EMAIL'"))')

if [ -n "$HAS_PERMISSION" ]; then
  echo "Service account '$SERVICE_ACCOUNT_NAME' has the gmail.send permission."
else
  echo "Service account '$SERVICE_ACCOUNT_NAME' does NOT have the gmail.send permission."
fi


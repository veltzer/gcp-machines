#!/bin/bash -e
PROJECT_ID="veltzer-machines-id"
SERVICE_ACCOUNT_NAME="gae-machines-engine-sa"

echo "Creating service account..."
SERVICE_ACCOUNT_EMAIL=$(gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
    --display-name="$SERVICE_ACCOUNT_NAME" \
    --format="value(email)")

# Role Granting
echo "Granting roles to service account..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
    --role="roles/compute.admin"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
    --role="roles/datastore.user"

# Credential Download
echo "Creating and downloading credentials..."
gcloud iam service-accounts keys create credentials.json \
    --iam-account="$SERVICE_ACCOUNT_EMAIL"

echo "Service account created and configured successfully!"
echo "Credentials file: credentials.json"

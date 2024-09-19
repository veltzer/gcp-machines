#!/bin/bash -e
PROJECT_ID=$(./get_project_id.py)
SERVICE_ACCOUNT_NAME="gae-machines-engine-sa"

# Check if Service Account Exists
echo "Checking for existing service account..."
SERVICE_ACCOUNT_EXISTS=$(gcloud iam service-accounts list \
	--filter="displayName:$SERVICE_ACCOUNT_NAME" \
	--format="value(email)")

if [ -n "$SERVICE_ACCOUNT_EXISTS" ]
then
	echo "Service account '$SERVICE_ACCOUNT_NAME' exists. Deleting..."
	gcloud iam service-accounts delete "$SERVICE_ACCOUNT_EXISTS"
	echo "Service account deleted."
fi

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
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
	--member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
	--role="roles/iam.serviceAccountTokenCreator"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
	--member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
	--role="roles/iam.serviceAccountUser"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
	--member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
	--role="roles/cloudquotas.viewer"

# Credential Download
echo "Creating and downloading credentials..."
file_output="${HOME}/.credentials/${PROJECT_ID}.json"
gcloud iam service-accounts keys create "${file_output}" \
	--iam-account="$SERVICE_ACCOUNT_EMAIL"

echo "Created ${file_output}..."

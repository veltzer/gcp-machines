#!/usr/bin/env python
"""
Manages the service account for the application.
This script can create a service account, assign it the necessary roles,
and download its credentials.
"""

import os
import base64
import google.auth
from googleapiclient import discovery
from googleapiclient.errors import HttpError

SERVICE_ACCOUNT_NAME = "gae-machines-engine-sa"
ROLES = [
    "roles/compute.admin",
    "roles/datastore.user",
    "roles/iam.serviceAccountTokenCreator",
    "roles/iam.serviceAccountUser",
    "roles/cloudquotas.viewer",
]

def get_iam_client():
    """Initializes and returns an IAM API client."""
    credentials, _ = google.auth.default()
    return discovery.build("iam", "v1", credentials=credentials)

def get_crm_client():
    """Initializes and returns a Cloud Resource Manager API client."""
    credentials, _ = google.auth.default()
    return discovery.build("cloudresourcemanager", "v1", credentials=credentials)

def create_service_account(project_id, iam_client):
    """
    Creates a new service account, deleting any existing one with the same name.
    """
    account_id = SERVICE_ACCOUNT_NAME
    display_name = SERVICE_ACCOUNT_NAME
    email = f"{account_id}@{project_id}.iam.gserviceaccount.com"

    # Check if service account exists
    # pylint: disable=no-member
    accounts = iam_client.projects().serviceAccounts().list(
        name=f"projects/{project_id}"
    ).execute().get("accounts", [])
    existing_account = next((acc for acc in accounts if acc.get("email") == email), None)

    if existing_account:
        print(f"Service account '{email}' already exists. Deleting it first.")
        iam_client.projects().serviceAccounts().delete(
            name=existing_account["name"]
        ).execute()
        print("Existing service account deleted.")

    print(f"Creating service account '{email}'...")
    # pylint: disable=no-member
    iam_client.projects().serviceAccounts().create(
        name=f"projects/{project_id}",
        body={"accountId": account_id, "serviceAccount": {"displayName": display_name}},
    ).execute()
    print("Service account created successfully.")
    return email

def grant_roles(project_id, service_account_email, crm_client):
    """
    Grants the necessary IAM roles to the service account.
    """
    member = f"serviceAccount:{service_account_email}"
    print(f"Granting roles to '{member}'...")

    try:
        # pylint: disable=no-member
        policy = crm_client.projects().getIamPolicy(resource=project_id).execute()

        for role in ROLES:
            binding = next((b for b in policy["bindings"] if b["role"] == role), None)
            if binding:
                if member not in binding["members"]:
                    binding["members"].append(member)
            else:
                policy["bindings"].append({"role": role, "members": [member]})
            print(f"  - Added binding for role: {role}")

        # pylint: disable=no-member
        crm_client.projects().setIamPolicy(
            resource=project_id, body={"policy": policy}
        ).execute()
        print("All roles granted successfully.")

    except HttpError as e:
        print(f"Error setting IAM policy: {e}")
        raise

def create_and_download_key(project_id, service_account_email, iam_client):
    """
    Creates a new key for the service account and saves it to the credentials directory.
    """
    key_path = os.path.expanduser(f"~/.credentials/{project_id}.json")
    os.makedirs(os.path.dirname(key_path), exist_ok=True)

    print(f"Creating and downloading key to '{key_path}'...")
    try:
        # pylint: disable=no-member
        key = iam_client.projects().serviceAccounts().keys().create(
            name=f"projects/{project_id}/serviceAccounts/{service_account_email}",
            body={}
        ).execute()

        key_data = base64.b64decode(key["privateKeyData"]).decode("utf-8")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key_data)

        print(f"Key successfully created and saved to {key_path}")
        print("IMPORTANT: Set the GOOGLE_APPLICATION_CREDENTIALS environment variable to this path.")

    except HttpError as e:
        print(f"Error creating service account key: {e}")
        raise

def main():
    """Main execution flow."""
    _, project_id = google.auth.default()
    iam_client = get_iam_client()
    crm_client = get_crm_client()

    service_account_email = create_service_account(project_id, iam_client)
    grant_roles(project_id, service_account_email, crm_client)
    create_and_download_key(project_id, service_account_email, iam_client)

if __name__ == "__main__":
    main()

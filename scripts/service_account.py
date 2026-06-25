#!/usr/bin/env python
"""
Manage the application's service account and its IAM permissions.

A single tool with subcommands to create the service account, (re-)grant its
roles, show whether it exists, and list the roles it currently holds.

Commands:
    create  Create the service account (replacing any existing one), grant all
            roles, and download a key.
    grant   Grant all required roles to the existing service account, without
            recreating it or touching its key.
    show    Show the service account: whether it exists in the project, its
            email, and the email of the currently-active credentials.
    roles   List the roles currently bound to the service account in the
            project IAM policy.
    delete  Delete the service account if it exists.
"""

import argparse
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
    # securityReviewer lets the SA read (but not modify) the project IAM
    # policy, which is what the `roles` command and `machines.py` need.
    "roles/iam.securityReviewer",
]

def get_iam_client():
    """Initializes and returns an IAM API client."""
    credentials, _ = google.auth.default()
    return discovery.build("iam", "v1", credentials=credentials)

def get_crm_client():
    """Initializes and returns a Cloud Resource Manager API client."""
    credentials, _ = google.auth.default()
    return discovery.build("cloudresourcemanager", "v1", credentials=credentials)

def service_account_email(project_id):
    """
    Returns the deterministic email of our service account for this project.
    """
    return f"{SERVICE_ACCOUNT_NAME}@{project_id}.iam.gserviceaccount.com"

def find_service_account(project_id, iam_client):
    """
    Returns the service account resource dict if our SA exists in the project,
    or None if it does not.
    """
    email = service_account_email(project_id)
    # pylint: disable=no-member
    accounts = iam_client.projects().serviceAccounts().list(
        name=f"projects/{project_id}"
    ).execute().get("accounts", [])
    return next((acc for acc in accounts if acc.get("email") == email), None)

def create_service_account(project_id, iam_client):
    """
    Creates a new service account, deleting any existing one with the same name.
    """
    account_id = SERVICE_ACCOUNT_NAME
    email = service_account_email(project_id)

    existing_account = find_service_account(project_id, iam_client)
    if existing_account:
        print(f"Service account '{email}' already exists. Deleting it first.")
        # pylint: disable=no-member
        iam_client.projects().serviceAccounts().delete(
            name=existing_account["name"]
        ).execute()
        print("Existing service account deleted.")

    print(f"Creating service account '{email}'...")
    # pylint: disable=no-member
    iam_client.projects().serviceAccounts().create(
        name=f"projects/{project_id}",
        body={"accountId": account_id, "serviceAccount": {"displayName": SERVICE_ACCOUNT_NAME}},
    ).execute()
    print("Service account created successfully.")
    return email

def grant_roles(project_id, email, crm_client):
    """
    Grants the required IAM roles to the service account.
    """
    member = f"serviceAccount:{email}"
    print(f"Granting roles to '{member}'...")

    # pylint: disable=no-member
    policy = crm_client.projects().getIamPolicy(resource=project_id).execute()

    for role in ROLES:
        binding = next((b for b in policy.get("bindings", []) if b["role"] == role), None)
        if binding:
            if member not in binding["members"]:
                binding["members"].append(member)
        else:
            policy.setdefault("bindings", []).append({"role": role, "members": [member]})
        print(f"  - Added binding for role: {role}")

    # pylint: disable=no-member
    crm_client.projects().setIamPolicy(
        resource=project_id, body={"policy": policy}
    ).execute()
    print("All roles granted successfully.")

def list_granted_roles(project_id, email, crm_client):
    """
    Returns the sorted list of roles currently bound to the service account in
    the project IAM policy.

    Reading the policy needs roles/iam.securityReviewer; if the caller lacks it
    the API returns 403, which is surfaced as a clear, actionable message.
    """
    member = f"serviceAccount:{email}"
    try:
        # pylint: disable=no-member
        policy = crm_client.projects().getIamPolicy(resource=project_id).execute()
    except HttpError as e:
        if e.resp.status == 403:
            raise SystemExit(
                f"Permission denied reading the IAM policy of project {project_id}.\n"
                f"The active credentials need roles/iam.securityReviewer. Run "
                f"`service_account.py grant` (as a user who can modify IAM) to "
                f"give the service account that role, then retry."
            ) from e
        raise
    roles = {
        binding["role"]
        for binding in policy.get("bindings", [])
        if member in binding.get("members", [])
    }
    return sorted(roles)

def create_and_download_key(project_id, email, iam_client):
    """
    Creates a new key for the service account and saves it to the credentials directory.
    """
    key_path = os.path.expanduser(f"~/.credentials/{project_id}.json")
    os.makedirs(os.path.dirname(key_path), exist_ok=True)

    print(f"Creating and downloading key to '{key_path}'...")
    # pylint: disable=no-member
    key = iam_client.projects().serviceAccounts().keys().create(
        name=f"projects/{project_id}/serviceAccounts/{email}",
        body={}
    ).execute()

    key_data = base64.b64decode(key["privateKeyData"]).decode("utf-8")
    with open(key_path, "w", encoding="utf-8") as f:
        f.write(key_data)

    print(f"Key successfully created and saved to {key_path}")
    print("IMPORTANT: Set the GOOGLE_APPLICATION_CREDENTIALS environment variable to this path.")

def handle_create(_args, project_id):
    """Create the service account, grant roles, and download a key."""
    iam_client = get_iam_client()
    crm_client = get_crm_client()
    email = create_service_account(project_id, iam_client)
    grant_roles(project_id, email, crm_client)
    create_and_download_key(project_id, email, iam_client)

def handle_grant(_args, project_id):
    """Grant all roles to the existing service account without recreating it."""
    iam_client = get_iam_client()
    email = service_account_email(project_id)
    if find_service_account(project_id, iam_client) is None:
        raise SystemExit(
            f"Service account '{email}' does not exist. Run `create` first."
        )
    grant_roles(project_id, email, get_crm_client())

def handle_show(_args, project_id):
    """Show the service account and the active-credentials identity."""
    iam_client = get_iam_client()
    email = service_account_email(project_id)
    exists = find_service_account(project_id, iam_client) is not None

    print(f"Project:               {project_id}")
    print(f"Service account email: {email}")
    print(f"Exists in project:     {'yes' if exists else 'no'}")

    # The identity the script is currently authenticating as. This is only set
    # on service-account credentials, not on user (gcloud) credentials.
    credentials, _ = google.auth.default()
    active = getattr(credentials, "service_account_email", None)
    print(f"Active credentials:    {active or '(user credentials)'}")

def handle_roles(_args, project_id):
    """List the roles currently bound to the service account."""
    email = service_account_email(project_id)
    roles = list_granted_roles(project_id, email, get_crm_client())
    if not roles:
        print(f"Service account '{email}' has no roles in project {project_id}.")
        return
    for role in roles:
        print(role)

def handle_delete(_args, project_id):
    """Delete the service account if it exists."""
    iam_client = get_iam_client()
    email = service_account_email(project_id)
    existing_account = find_service_account(project_id, iam_client)
    if existing_account is None:
        print(f"Service account '{email}' does not exist. Nothing to delete.")
        return
    print(f"Deleting service account '{email}'...")
    # pylint: disable=no-member
    iam_client.projects().serviceAccounts().delete(
        name=existing_account["name"]
    ).execute()
    print("Service account deleted.")

def main():
    """Main entry point and command-line parser."""
    parser = argparse.ArgumentParser(
        description="Manage the application service account and its IAM permissions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    commands = {
        "create": (handle_create, "Create the service account, grant roles, and download a key."),
        "grant": (handle_grant, "Grant all roles to the existing service account."),
        "show": (handle_show, "Show the service account and active-credentials identity."),
        "roles": (handle_roles, "List the roles the service account currently has."),
        "delete": (handle_delete, "Delete the service account if it exists."),
    }
    for name, (func, help_text) in commands.items():
        sub = subparsers.add_parser(name, help=help_text)
        sub.set_defaults(func=func)

    args = parser.parse_args()
    _, project_id = google.auth.default()
    args.func(args, project_id)

if __name__ == "__main__":
    main()

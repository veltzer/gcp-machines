#!/usr/bin/env python

"""
List all roles assigned to a service account
"""

import os
import json
import google.auth
from googleapiclient import discovery

def get_service_account_email():
    """Get service account email from credentials file"""
    credentials_file = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    with open(credentials_file, encoding="utf8") as f:
        data = json.load(f)
        return data["client_email"]

def list_roles(project_id: str, service_account_email: str) -> None:
    """
    Lists all roles assigned to a service account
    """
    credentials, _ = google.auth.default()
    service = discovery.build('cloudresourcemanager', 'v1', credentials=credentials)

    # Get IAM policy
    policy = service.projects().getIamPolicy(resource=project_id).execute()

    # Find roles for the service account
    roles = set()
    for binding in policy.get('bindings', []):
        members = binding.get('members', [])
        service_account_member = f"serviceAccount:{service_account_email}"

        if service_account_member in members:
            roles.add(binding['role'])

    # Print sorted roles
    for role in sorted(roles):
        print(role)

def main():
    """main entry point"""
    _, project_id = google.auth.default()
    service_account_email = get_service_account_email()
    list_roles(project_id, service_account_email)

if __name__ == "__main__":
    main()

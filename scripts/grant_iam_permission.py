#!/usr/bin/env python

"""
Grant IAM permissions to service account to read IAM policies
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

def grant_iam_permission(project_id: str, service_account_email: str):
    """Grant Security Reviewer role to service account"""
    credentials, _ = google.auth.default()
    service = discovery.build('cloudresourcemanager', 'v1', credentials=credentials)

    # Get current IAM policy
    policy = service.projects().getIamPolicy(resource=project_id).execute()

    # Add the service account to Security Reviewer role
    member = f"serviceAccount:{service_account_email}"
    role = "roles/iam.securityReviewer"

    # Find existing binding or create new one
    binding_found = False
    for binding in policy.get('bindings', []):
        if binding['role'] == role:
            if member not in binding['members']:
                binding['members'].append(member)
            binding_found = True
            break

    if not binding_found:
        if 'bindings' not in policy:
            policy['bindings'] = []
        policy['bindings'].append({
            'role': role,
            'members': [member]
        })

    # Set the updated policy
    set_policy_request = {'policy': policy}
    service.projects().setIamPolicy(resource=project_id, body=set_policy_request).execute()
    print(f"Granted {role} to {service_account_email}")

def main():
    """main entry point"""
    _, project_id = google.auth.default()
    service_account_email = get_service_account_email()
    grant_iam_permission(project_id, service_account_email)

if __name__ == "__main__":
    main()

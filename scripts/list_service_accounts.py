#!/usr/bin/env python
"""
List all service accounts associated with the current GCP project.

Run this under the default (personal) account, not under a service-account
key, since it is meant to give you an overview of every service account in
the project.
"""

import sys
import google.auth
from googleapiclient import discovery

def require_default_account(credentials):
    """
    Refuses to run unless we are authenticating as the default (personal)
    account rather than a service account.

    Service-account credentials carry a service_account_email; user (default)
    credentials do not. If a service account is active it is because
    GOOGLE_APPLICATION_CREDENTIALS points at its key, so that is what the user
    must change.
    """
    sa_email = getattr(credentials, "service_account_email", None)
    if sa_email is not None:
        sys.exit(
            f"Refusing to run as service account '{sa_email}'.\n"
            "This script must run as your default (personal) account.\n"
            "Unset GOOGLE_APPLICATION_CREDENTIALS (or open a fresh shell) so "
            "google.auth uses your default account, then re-run."
        )

def list_service_accounts(project_id, iam_client):
    """
    Returns the list of service account resources in the project.
    """
    accounts = []
    # pylint: disable=no-member
    request = iam_client.projects().serviceAccounts().list(name=f"projects/{project_id}")
    while request is not None:
        response = request.execute()
        accounts.extend(response.get("accounts", []))
        request = iam_client.projects().serviceAccounts().list_next(
            previous_request=request, previous_response=response
        )
    return accounts

def print_service_accounts_table(accounts):
    """
    Prints service accounts as an aligned EMAIL / DISABLED / DISPLAY NAME table.
    """
    headers = ("EMAIL", "DISABLED", "DISPLAY NAME")
    rows = [
        (acc.get("email", "N/A"),
         "yes" if acc.get("disabled", False) else "no",
         acc.get("displayName", ""))
        for acc in accounts
    ]
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(2)] if rows \
        else [len(h) for h in headers[:2]]
    print(f"{headers[0]:<{widths[0]}}  {headers[1]:<{widths[1]}}  {headers[2]}")
    for email, disabled, display_name in rows:
        print(f"{email:<{widths[0]}}  {disabled:<{widths[1]}}  {display_name}")

def main():
    """Main entry point."""
    try:
        credentials, project_id = google.auth.default()
    except google.auth.exceptions.DefaultCredentialsError:
        sys.exit(
            "No credentials found for your default account.\n"
            "Run `gcloud auth application-default login` once to set up "
            "Application Default Credentials, then re-run."
        )
    require_default_account(credentials)
    iam_client = discovery.build("iam", "v1", credentials=credentials)
    accounts = list_service_accounts(project_id, iam_client)
    print_service_accounts_table(accounts)

if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""
Manage Identity-Aware Proxy (IAP) access to the App Engine app.

Enabling IAP itself is a one-time manual step in the GCP console (see
doc/iap.md). Once it is on, this script manages which users may pass through
it by granting/revoking the "IAP-secured Web App User" role
(roles/iap.httpsResourceAccessor) on the App Engine IAP resource.

Student emails are read from data.gi/student_emails.txt, one email per line
(blank lines and lines starting with # are ignored). That file is git-ignored
on purpose: this repository is public and student emails are private data.
"""

import argparse
import os
import sys
import google.auth
from googleapiclient import discovery
from googleapiclient.errors import HttpError

# Always read the student emails from data.gi/student_emails.txt at the repo root.
STUDENT_EMAILS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data.gi",
    "student_emails.txt",
)

# The role IAP checks before letting a user through to the app.
ROLE = "roles/iap.httpsResourceAccessor"

def require_default_account(credentials):
    """
    Refuses to run unless we are authenticating as the default (personal)
    account rather than a service account.

    This project is administered as the project owner (the default account);
    the service account is only for the deployed app. Service-account
    credentials carry a service_account_email; user (default) credentials do
    not.
    """
    sa_email = getattr(credentials, "service_account_email", None)
    if sa_email is not None:
        sys.exit(
            f"Refusing to run as service account '{sa_email}'.\n"
            "This script must run as your default (personal) account.\n"
            "Set gcp_identity=default in .gcp.conf (or unset "
            "GOOGLE_APPLICATION_CREDENTIALS / open a fresh shell), then re-run."
        )

def get_project_number(project_id, credentials):
    """
    Returns the numeric project number for a project id. The IAP IAM resource
    path is canonically built from the project number.
    """
    crm = discovery.build("cloudresourcemanager", "v1", credentials=credentials)
    project = crm.projects().get(projectId=project_id).execute()
    return project["projectNumber"]

def iap_resource(project_number, project_id):
    """
    Returns the IAM resource path of the IAP-protected App Engine app.
    """
    return f"projects/{project_number}/iap_web/appengine-{project_id}"

def read_student_emails():
    """
    Reads the student emails file and returns a list of emails. Blank lines
    and lines starting with # are ignored.
    """
    if not os.path.exists(STUDENT_EMAILS_FILE):
        sys.exit(
            f"Missing {STUDENT_EMAILS_FILE}\n"
            "Create it with one student email per line "
            "(see the show-input-sample command).\n"
            "It is git-ignored and will stay out of the public repository."
        )
    emails = []
    with open(STUDENT_EMAILS_FILE, "r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line and not line.startswith("#"):
                emails.append(line)
    return emails

def get_policy(iap, resource):
    """
    Returns the current IAM policy of the IAP resource.
    """
    return iap.v1().getIamPolicy(resource=resource, body={}).execute()

def set_policy(iap, resource, policy):
    """
    Replaces the IAM policy of the IAP resource. The policy carries the etag
    from get_policy so concurrent modifications fail instead of clobbering.
    """
    return iap.v1().setIamPolicy(resource=resource, body={"policy": policy}).execute()

def get_role_binding(policy):
    """
    Returns the members list of the access-role binding inside a policy,
    creating an empty binding in the policy if there is none.
    """
    for binding in policy.setdefault("bindings", []):
        if binding["role"] == ROLE:
            return binding["members"]
    binding = {"role": ROLE, "members": []}
    policy["bindings"].append(binding)
    return binding["members"]

def iap_enabled(project_id, credentials):
    """
    Returns whether IAP is currently enabled on the App Engine app, or None
    if that could not be determined.
    """
    try:
        appengine = discovery.build("appengine", "v1", credentials=credentials)
        app_info = appengine.apps().get(appsId=project_id).execute()
        return bool(app_info.get("iap", {}).get("enabled", False))
    except Exception:  # pylint: disable=broad-exception-caught
        return None

def status(project_id, iap, resource, credentials):
    """
    Prints whether IAP is enabled and who currently holds web access.
    """
    enabled = iap_enabled(project_id, credentials)
    if enabled is None:
        print("IAP enabled: unknown (could not query the App Engine API)")
    else:
        print(f"IAP enabled: {'yes' if enabled else 'NO - the app is open to the world (see doc/iap.md)'}")
    members = get_role_binding(get_policy(iap, resource))
    if members:
        print(f"Members with {ROLE}:")
        for member in sorted(members):
            print(f"  {member}")
    else:
        print(f"No members hold {ROLE}; nobody can enter the app while IAP is on.")

def grant(iap, resource, emails):
    """
    Grants IAP web access to the given emails. Already-granted emails are
    skipped. Prints what happened.
    """
    policy = get_policy(iap, resource)
    members = get_role_binding(policy)
    added = []
    for email in emails:
        member = f"user:{email}"
        if member in members:
            print(f"Already granted: {email}")
        else:
            members.append(member)
            added.append(email)
    if not added:
        print("Nothing to do.")
        return
    set_policy(iap, resource, policy)
    for email in added:
        print(f"Granted: {email}")

def revoke(iap, resource, emails):
    """
    Revokes IAP web access from the given emails. Prints what happened.
    """
    policy = get_policy(iap, resource)
    members = get_role_binding(policy)
    removed = []
    for email in emails:
        member = f"user:{email}"
        if member in members:
            members.remove(member)
            removed.append(email)
        else:
            print(f"Was not granted anyway: {email}")
    if not removed:
        print("Nothing to do.")
        return
    set_policy(iap, resource, policy)
    for email in removed:
        print(f"Revoked: {email}")

def sync(iap, resource, prune):
    """
    Grants access to every email in the student emails file. With prune, also
    revokes user: members that are not in the file (other member types, like
    serviceAccount: or domain:, are never touched).
    """
    emails = read_student_emails()
    policy = get_policy(iap, resource)
    members = get_role_binding(policy)
    wanted = {f"user:{email}" for email in emails}
    current_users = {member for member in members if member.startswith("user:")}
    to_add = sorted(wanted - current_users)
    to_remove = sorted(current_users - wanted) if prune else []
    if not to_add and not to_remove:
        print(f"Already in sync: {len(wanted)} students have access.")
        return
    for member in to_add:
        members.append(member)
    for member in to_remove:
        members.remove(member)
    set_policy(iap, resource, policy)
    for member in to_add:
        print(f"Granted: {member.removeprefix('user:')}")
    for member in to_remove:
        print(f"Revoked: {member.removeprefix('user:')}")
    extra = sorted(current_users - wanted) if not prune else []
    for member in extra:
        print(f"Not in file (kept, use --prune to revoke): {member.removeprefix('user:')}")

def list_brands(iap, project_number):
    """
    Returns the OAuth brands (consent screens) of the project. A project has
    at most one.
    """
    try:
        response = iap.projects().brands().list(parent=f"projects/{project_number}").execute()
    except HttpError as error:
        if "organization" in str(error):
            sys.exit(
                "GCP refuses brand (consent screen) API calls for projects that\n"
                "do not belong to an organization; this project is owned by a\n"
                "personal account. Configure the OAuth consent screen once in\n"
                "the console instead: APIs & Services -> OAuth consent screen\n"
                "(see doc/iap.md)."
            )
        raise
    return response.get("brands", [])

def print_brand(brand):
    """
    Prints one OAuth brand in a readable form.
    """
    print(f"  name: {brand['name']}")
    print(f"  title: {brand.get('applicationTitle', 'N/A')}")
    print(f"  support email: {brand.get('supportEmail', 'N/A')}")
    internal = brand.get("orgInternalOnly", False)
    print(f"  internal only: {internal}")
    if internal:
        print(
            "  NOTE: internal-only brands reject plain gmail users; make the app\n"
            "  External and publish it in the console OAuth consent screen page."
        )

def consent_status(iap, project_number):
    """
    Prints the OAuth consent screen (brand) of the project, if any.
    """
    brands = list_brands(iap, project_number)
    if not brands:
        print("No OAuth consent screen (brand) configured yet; run consent-create.")
        return
    for brand in brands:
        print("OAuth consent screen:")
        print_brand(brand)

def consent_create(iap, project_number, title, email):
    """
    Creates the OAuth consent screen (brand) of the project unless one
    already exists. The support email must be owned by the account running
    this script.
    """
    brands = list_brands(iap, project_number)
    if brands:
        print("An OAuth consent screen already exists, not creating another:")
        for brand in brands:
            print_brand(brand)
        return
    brand = iap.projects().brands().create(
        parent=f"projects/{project_number}",
        body={"applicationTitle": title, "supportEmail": email},
    ).execute()
    print("Created OAuth consent screen:")
    print_brand(brand)

def show_input_sample():
    """
    Prints a sample of the input expected in data.gi/student_emails.txt.
    """
    print("# one student email per line; # starts a comment")
    for name in ("alice", "bob", "carol"):
        print(f"{name}@gmail.com")

def main():
    """Main entry point and command-line parser."""
    parser = argparse.ArgumentParser(description="Manage IAP access to the App Engine app.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Status command
    status_parser = subparsers.add_parser("status", help="Show whether IAP is on and who has access.")
    status_parser.set_defaults(func=lambda args, ctx: status(ctx["project_id"], ctx["iap"], ctx["resource"], ctx["credentials"]))

    # Grant command
    grant_parser = subparsers.add_parser("grant", help="Grant IAP access to one or more emails.")
    grant_parser.add_argument("emails", nargs="+", help="Email addresses to grant access to.")
    grant_parser.set_defaults(func=lambda args, ctx: grant(ctx["iap"], ctx["resource"], args.emails))

    # Revoke command
    revoke_parser = subparsers.add_parser("revoke", help="Revoke IAP access from one or more emails.")
    revoke_parser.add_argument("emails", nargs="+", help="Email addresses to revoke access from.")
    revoke_parser.set_defaults(func=lambda args, ctx: revoke(ctx["iap"], ctx["resource"], args.emails))

    # Sync command
    sync_parser = subparsers.add_parser(
        "sync",
        help=f"Grant access to every email in {STUDENT_EMAILS_FILE}.",
    )
    sync_parser.add_argument(
        "--prune",
        action="store_true",
        help="Also revoke user grants that are not in the file.",
    )
    sync_parser.set_defaults(func=lambda args, ctx: sync(ctx["iap"], ctx["resource"], args.prune))

    # Consent-status command
    consent_status_parser = subparsers.add_parser(
        "consent-status",
        help="Show the OAuth consent screen (brand) of the project.",
    )
    consent_status_parser.set_defaults(func=lambda args, ctx: consent_status(ctx["iap"], ctx["project_number"]))

    # Consent-create command
    consent_create_parser = subparsers.add_parser(
        "consent-create",
        help="Create the OAuth consent screen (brand) needed by IAP.",
    )
    consent_create_parser.add_argument("--title", required=True, help="Application title shown on the login page.")
    consent_create_parser.add_argument("--email", required=True, help="Support email (must be owned by you).")
    consent_create_parser.set_defaults(
        func=lambda args, ctx: consent_create(ctx["iap"], ctx["project_number"], args.title, args.email)
    )

    # Show-input-sample command
    sample_parser = subparsers.add_parser(
        "show-input-sample",
        help="Show a sample of the expected student emails file.",
    )
    sample_parser.set_defaults(func=lambda args, ctx: show_input_sample())

    args = parser.parse_args()
    credentials, project_id = google.auth.default()
    require_default_account(credentials)
    iap = discovery.build("iap", "v1", credentials=credentials)
    project_number = get_project_number(project_id, credentials)
    context = {
        "credentials": credentials,
        "project_id": project_id,
        "project_number": project_number,
        "iap": iap,
        "resource": iap_resource(project_number, project_id),
    }
    args.func(args, context)

if __name__ == "__main__":
    main()

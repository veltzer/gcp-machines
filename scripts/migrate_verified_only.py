#!/usr/bin/env python
"""
One-off migration to "only a proven email gets an account" (see src/users.py).

Before, an email and password login was made at sign-up and signed in before
its address was proven: a login with verified false, joined to no user. Now
a login exists only for a proven email, so every login has a user, and
nothing of the old verification (account_id, verification_sent,
verification_token, verified) is kept.

This finds the logins that are not proven (an email login with verified
false, or any login with no user) and deletes them, with what they carry.
It strips the removed fields from the logins that stay. The users stay as
they are: none of them came from an unproven login.

An unproven login may carry an admin's assignment (owner or machine_limit;
the Students table could assign one to a login with no user): a student may
have machines under it. Those are never deleted: they are listed, nothing
at all is changed, and the exit status is 1, so an admin can decide. Give
the assignment to the user of the same email (a student who has signed in
properly has one) or clear it on the Students table, then run again.

Prints what it would do; pass --apply to do it. Safe to run again: what is
done is not found again.
"""

import argparse
import sys

import google.auth
from google.cloud import datastore

# what the old verification kept on a login and the app no longer reads
REMOVED_FIELDS = ("account_id", "verification_sent", "verification_token", "verified")

# what an admin assigns
ASSIGNED_FIELDS = ("owner", "machine_limit")


def require_default_account(credentials):
    """ refuses to run as a service account; this project is administered as its owner """
    sa_email = getattr(credentials, "service_account_email", None)
    if sa_email is not None:
        sys.exit(f"Refusing to run as service account '{sa_email}'. Set gcp_identity=default in .gcp.conf.")


def unproven(login):
    """
    Whether a login's email was never proven: it has no user, or it is an
    email login marked unverified. A login that lost its verified field
    already (see REMOVED_FIELDS) is proven by having a user.
    """
    if not login.get("user"):
        return True
    return login.key.name.startswith("email:") and "verified" in login and not login["verified"]


def key_name(entity: datastore.Entity) -> str:
    """ The key name of a stored entity: one read from the datastore always has a key. """
    if entity.key is None:
        raise ValueError("an entity without a key")
    name: str = entity.key.name
    return name


def plan(logins):
    """ (logins to delete, logins to strip, unproven logins that carry an assignment) """
    delete: list[datastore.Entity] = []
    strip: list[datastore.Entity] = []
    assigned: list[datastore.Entity] = []
    for login in logins:
        if not unproven(login):
            if any(field in login for field in REMOVED_FIELDS):
                strip.append(login)
        elif any(login.get(field) is not None for field in ASSIGNED_FIELDS):
            assigned.append(login)
        else:
            delete.append(login)
    return delete, strip, assigned


def main():
    parser = argparse.ArgumentParser(description="Remove unproven logins and the fields of the old email verification.")
    parser.add_argument("--apply", action="store_true", help="Write the change (default: only show it).")
    args = parser.parse_args()
    credentials, project_id = google.auth.default()
    require_default_account(credentials)
    client = datastore.Client(project=project_id)
    delete, strip, assigned = plan(list(client.query(kind="login").fetch()))
    for login in delete:
        reason = "no user" if not login.get("user") else "email not verified"
        print(f"delete login {key_name(login)} ({reason})")
    for login in strip:
        removed = ", ".join(field for field in REMOVED_FIELDS if field in login)
        print(f"strip login {key_name(login)}: {removed}")
    for login in assigned:
        given = ", ".join(f"{field} {login[field]}" for field in ASSIGNED_FIELDS if login.get(field) is not None)
        print(f"KEEP login {key_name(login)}: unproven, but assigned {given}")
    print(f"logins to delete: {len(delete)}, to strip: {len(strip)}, unproven with an assignment: {len(assigned)}")
    if assigned:
        sys.exit("Unproven logins carry an assignment (KEEP above): nothing was changed. Decide what to do with them, then rerun.")
    if not args.apply:
        print("Dry run; pass --apply to do it.")
        return
    for login in strip:
        for field in REMOVED_FIELDS:
            login.pop(field, None)
    client.put_multi(strip)
    client.delete_multi([login.key for login in delete])
    print("Done.")


if __name__ == "__main__":
    main()

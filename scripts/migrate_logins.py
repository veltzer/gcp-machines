#!/usr/bin/env python
"""
One-off migration to one user per person (see src/users.py).

Before, every way of signing in was its own account: a Datastore entity of
kind "user" keyed by subject (google:<sub>, github:<id>, email:<address>,
dev:<address>). Now each of those is a login (kind "login", same key name),
and every verified email gets one user (kind "user", keyed by the email)
that all its logins join.

For each old account this writes its login, joined to the user of its email
when that email is verified. An admin's assignment moves to the user, or
stays on the login when the login has no user (an unverified password
account). When two logins of one email carry different assignments nothing
is written: the conflicts are listed for an admin to settle first. Then the
old entities are deleted.

Prints what it would do; pass --apply to do it. Safe to run again: logins
that exist already are left alone.
"""

import argparse
import sys

import google.auth
from google.cloud import datastore


def require_default_account(credentials):
    """ refuses to run as a service account; this project is administered as its owner """
    sa_email = getattr(credentials, "service_account_email", None)
    if sa_email is not None:
        sys.exit(f"Refusing to run as service account '{sa_email}'. Set gcp_identity=default in .gcp.conf.")


def key_name(entity: datastore.Entity) -> str:
    """ The key name of a stored entity: one read from the datastore always has a key. """
    if entity.key is None:
        raise ValueError("an entity without a key")
    name: str = entity.key.name
    return name


def plan(client):
    """ (logins, users, legacy keys to delete) to write, or exits listing assignment conflicts """
    legacy = [entity for entity in client.query(kind="user").fetch() if ":" in entity.key.name]
    logins = []
    users: dict[str, datastore.Entity] = {}
    owners = {}
    for old in legacy:
        if client.get(client.key("login", old.key.name)) is not None:
            print(f"login {old.key.name} exists already, left alone")
            continue
        login = datastore.Entity(
            key=client.key("login", old.key.name),
            exclude_from_indexes=tuple(old.exclude_from_indexes),
        )
        login.update({field: value for field, value in old.items() if field != "owner"})
        email = (old.get("email") or "").strip().lower()
        if old.get("verified") and email:
            login["user"] = email
            user = users.get(email)
            if user is None:
                user = client.get(client.key("user", email)) or datastore.Entity(
                    key=client.key("user", email), exclude_from_indexes=("picture",))
                user.setdefault("email", email)
                users[email] = user
            for field in ("name", "picture"):
                if not user.get(field) and old.get(field):
                    user[field] = old[field]
            for field, pick in (("created", min), ("last_login", max)):
                if old.get(field):
                    user[field] = pick(filter(None, (user.get(field), old[field])))
            if old.get("owner"):
                owners.setdefault(email, {})[old.key.name] = old["owner"]
        elif old.get("owner"):
            login["owner"] = old["owner"]
        logins.append(login)
    conflicts = {email: by for email, by in owners.items() if len(set(by.values())) > 1}
    if conflicts:
        for email, by in conflicts.items():
            print(f"{email}: " + ", ".join(f"{subject} -> {owner}" for subject, owner in sorted(by.items())))
        sys.exit("Different assignments for one email (above): settle them on the Students table, then rerun.")
    for email, by in owners.items():
        owner = next(iter(by.values()))
        if users[email].get("owner") not in (None, owner):
            sys.exit(f"{email}: user is assigned {users[email]['owner']}, an old account {owner}; settle it first.")
        users[email]["owner"] = owner
    return logins, list(users.values()), [old.key for old in legacy]


def main():
    parser = argparse.ArgumentParser(description="Migrate old per-sign-in accounts to users and their logins.")
    parser.add_argument("--apply", action="store_true", help="Write the change (default: only show it).")
    args = parser.parse_args()
    credentials, project_id = google.auth.default()
    require_default_account(credentials)
    client = datastore.Client(project=project_id)
    logins, users, legacy = plan(client)
    for login in logins:
        joined = f"joins user {login['user']}" if login.get("user") else "no user (email not verified)"
        assigned = f", assigned {login['owner']}" if login.get("owner") else ""
        if login.key is None:
            raise ValueError("login entity has no key")
        print(f"login {login.key.name}: {joined}{assigned}")
    for user in users:
        assigned = f", assigned {user['owner']}" if user.get("owner") else ""
        print(f"user {key_name(user)}{assigned}")
    print(f"old accounts to delete: {len(legacy)}")
    if not args.apply:
        print("Dry run; pass --apply to do it.")
        return
    with client.transaction():
        client.put_multi([*users, *logins])
    client.delete_multi(legacy)
    print("Done.")


if __name__ == "__main__":
    main()

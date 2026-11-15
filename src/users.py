"""
User accounts, kept in Datastore (kind "user").

The key name of a user entity is a provider-qualified subject, the stable
identity the user signed in with:

    google:<sub>      Sign in with Google (the ID token "sub" claim)
    github:<id>       Continue with GitHub (the numeric GitHub user id)
    email:<address>   email and password, registered here
    dev:<address>     the local development login (never on Cloud Run)

Accounts are found by subject, never by email: an email can change hands at
a provider, and an email typed into the password form proves nothing. So the
same address used with Google and with a password is two accounts. Whether
an account's email is trustworthy is kept in the "verified" field, true for
Google and GitHub (both only hand over verified addresses), and for password
accounts false until the link mailed to the address is followed (see
verification.py).

Fields: email, name, picture, verified, owner (the machine owner label an
admin assigned to this account, absent until then), created, last_login,
and for password accounts only:

    password_hash        werkzeug hash of the password
    account_id           random, new each time the account is (re)created;
                         a session counts only while it carries the same
                         one, so replacing an unverified account signs out
                         whoever was signed in to the old one
    verification_sent    when the last verification mail went out
    verification_token   the secret in that mail's link; see
                         auth.check_verification
"""

import datetime
import secrets

from google.cloud import datastore

KIND = "user"

# never indexed: nobody queries by them, and an index on the hash would be a
# second copy of it
UNINDEXED = ("password_hash", "picture", "account_id", "verification_token")


def now():
    return datetime.datetime.now(datetime.UTC)


def get(client, subject):
    """ the user entity for a subject, or None """
    return client.get(client.key(KIND, subject))


def upsert_from_provider(client, subject, email, name, picture, verified):
    """
    Find or create the account of a third-party sign-in and refresh its
    profile fields, which the provider owns. Returns the entity.
    """
    with client.transaction():
        entity = get(client, subject)
        if entity is None:
            entity = datastore.Entity(key=client.key(KIND, subject), exclude_from_indexes=UNINDEXED)
            entity["created"] = now()
        entity["email"] = email.strip().lower()
        entity["name"] = name
        entity["picture"] = picture
        entity["verified"] = verified
        entity["last_login"] = now()
        client.put(entity)
    return entity


def create_password_account(client, email, password_hash):
    """
    Register an email and password account. Returns the new entity, or None
    when the address already has a verified password account.

    An unverified one is replaced: it proves nothing about who owns the
    address, so it must not keep the address from its owner, who may also
    simply have forgotten the password before verifying. Nothing of it
    carries over, an admin's assignment included, except the pace of the
    mails and the secret of the last one. Keeping that secret is safe: it is
    only in the mailbox, and it verifies only a session that also knows the
    new password (see auth.check_verification). It lets the address's owner
    use the mail they already have.
    """
    subject = f"email:{email}"
    with client.transaction():
        existing = get(client, subject)
        if existing is not None and existing.get("verified"):
            return None
        entity = datastore.Entity(key=client.key(KIND, subject), exclude_from_indexes=UNINDEXED)
        entity.update({
            "email": email,
            "name": email.split("@")[0],
            "picture": "",
            "verified": False,
            "password_hash": password_hash,
            "account_id": secrets.token_hex(16),
            "created": now(),
            "last_login": now(),
        })
        if existing is not None:
            for field in ("verification_sent", "verification_token"):
                if field in existing:
                    entity[field] = existing[field]
        client.put(entity)
    return entity


def touch(client, entity):
    """ record a login on an existing account """
    entity["last_login"] = now()
    client.put(entity)


def mark_verification_sent(client, entity, token):
    """ record when the verification mail went out (which paces resending) and its link's secret """
    entity["verification_sent"] = now()
    entity["verification_token"] = token
    client.put(entity)


def mark_verified(client, entity):
    """ the account's email was shown to be its owner's """
    entity["verified"] = True
    entity.pop("verification_sent", None)
    entity.pop("verification_token", None)
    client.put(entity)


def set_owner(client, subject, owner):
    """
    Assign a machine owner label to an account, or with an empty owner clear
    the assignment. Returns False when there is no such account.
    """
    with client.transaction():
        entity = get(client, subject)
        if entity is None:
            return False
        if owner:
            entity["owner"] = owner
        else:
            entity.pop("owner", None)
        client.put(entity)
    return True

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

Fields: email, name, picture, verified, password_hash and verification_sent
(password accounts only), owner (the machine owner label an admin assigned
to this account, absent until then), created, last_login.
"""

import datetime

from google.cloud import datastore

KIND = "user"

# never indexed: nobody queries by them, and an index on the hash would be a
# second copy of it
UNINDEXED = ("password_hash", "picture")


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
    carries over, an admin's assignment included.
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
            "created": now(),
            "last_login": now(),
        })
        # replacing the account must not reset the pace of the mails to the address
        if existing is not None and "verification_sent" in existing:
            entity["verification_sent"] = existing["verification_sent"]
        client.put(entity)
    return entity


def touch(client, entity):
    """ record a login on an existing account """
    entity["last_login"] = now()
    client.put(entity)


def mark_verification_sent(client, entity):
    """ record when the verification mail went out, which paces resending """
    entity["verification_sent"] = now()
    client.put(entity)


def mark_verified(client, entity):
    """ the account's email was shown to be its owner's """
    entity["verified"] = True
    entity.pop("verification_sent", None)
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

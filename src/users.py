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
Google and GitHub (both only hand over verified addresses) and false for
password accounts.

Fields: email, name, picture, verified, password_hash (password accounts
only), owner (the machine owner label an admin assigned to this account,
absent until then), created, last_login.
"""

import datetime

from google.cloud import datastore

KIND = "user"

# never indexed: nobody queries by them, and an index on the hash would be a
# second copy of it
UNINDEXED = ("password_hash", "picture")


def _now():
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
            entity["created"] = _now()
        entity["email"] = email.strip().lower()
        entity["name"] = name
        entity["picture"] = picture
        entity["verified"] = verified
        entity["last_login"] = _now()
        client.put(entity)
    return entity


def create_password_account(client, email, password_hash):
    """
    Register an email and password account. Returns the new entity, or None
    when the address already has a password account.
    """
    subject = f"email:{email}"
    with client.transaction():
        if get(client, subject) is not None:
            return None
        entity = datastore.Entity(key=client.key(KIND, subject), exclude_from_indexes=UNINDEXED)
        entity.update({
            "email": email,
            "name": email.split("@")[0],
            "picture": "",
            "verified": False,
            "password_hash": password_hash,
            "created": _now(),
            "last_login": _now(),
        })
        client.put(entity)
    return entity


def touch(client, entity):
    """ record a login on an existing account """
    entity["last_login"] = _now()
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

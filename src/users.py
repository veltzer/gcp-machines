"""
Users and their logins, kept in Datastore.

A login (kind "login") is one way of signing in. Its key name is the
provider-qualified subject, the stable identity the provider vouches for:

    google:<sub>      Sign in with Google (the ID token "sub" claim)
    github:<id>       Continue with GitHub (the numeric GitHub user id)
    email:<address>   email and password, the address proven by a mailed link
    dev:<address>     the local development login (never on Cloud Run)

A user (kind "user", key name = the lowercased email) is the person: the
same person signing in with Google, GitHub or an email is one user, and
what belongs to them (an admin's machine assignment) lives on the user.

A login exists only for an email that is proven to be the person's: Google's
email_verified, GitHub's verified primary address, or for an email login the
mailed link followed (see verification.py). So every login has a user, the
user of its email, which it joins when it is made. A provider that does not
vouch for the email gets no login and no user at all (UnverifiedEmailError).
An address merely typed into a form proves nothing and can be used for
nothing: otherwise anyone could register a student's address and walk into
their account. An email login's password is chosen only on the page the
mailed link leads to, so only whoever reads the mailbox ever sets it. A login
joins once and stays: should the provider's address change later, the login
keeps its user rather than moving to another.

Login fields: email (as the provider last reported it), name, picture, user
(the key name of its user), created, last_login, and for email logins only
password_hash (the werkzeug hash of the password).

A pending verification (kind "email_verification", key name = the address)
is a link mailed to an address that has not been followed yet: token_hash
(the SHA-256 of the link's secret, which is only in the mail) and sent_at.
There is at most one per address, a new link replaces it, and following the
link deletes it.

User fields: email, name, picture, owner (the machine owner label an admin
assigned, absent until then), machine_limit (how many machines the user may
have at once, absent for DEFAULT_MACHINE_LIMIT), created, last_login.
"""

import dataclasses
import datetime as dt

from google.cloud import datastore

LOGIN = "login"
USER = "user"
VERIFICATION = "email_verification"

# how many machines a user may have at once unless an admin set otherwise,
# and the most an admin may allow
DEFAULT_MACHINE_LIMIT = 1
MAX_MACHINE_LIMIT = 10

# never indexed: nobody queries by them, and an index on the hash would be a
# second copy of it
UNINDEXED = ("password_hash", "picture")


def key_name(entity):
    """ the name of a stored entity's key; an entity read from Datastore always has one """
    if entity.key is None:
        raise ValueError("entity has no key")
    return entity.key.name


def now():
    return dt.datetime.now(dt.UTC)


class UnverifiedEmailError(Exception):
    """ the provider does not vouch for the person's email, so they get no account """


@dataclasses.dataclass(frozen=True)
class Account:
    """
    Who is signed in: the login they used and the user it belongs to. The
    Students table also makes one of a user alone, with no login.
    """

    login: datastore.Entity | None
    user: datastore.Entity

    @property
    def subject(self):
        return key_name(self.login) if self.login is not None else ""

    @property
    def method(self):
        """ "google", "github", "email" or "dev" """
        return self.subject.split(":", 1)[0]

    @property
    def email(self):
        return key_name(self.user)

    @property
    def name(self):
        """ what to call this person: the name they set or a provider gave, else their email's local part """
        return self.user.get("name") or self.email.split("@")[0]

    @property
    def picture(self):
        """ the photo of the login signed in with, else the user's (from whichever login brought one), else "" """
        for entity in (self.login, self.user):
            if entity is not None and entity.get("picture"):
                return entity["picture"]
        return ""

    @property
    def owner(self):
        """ the machine owner label an admin assigned, or None """
        return self.user.get("owner")

    @property
    def machine_limit(self):
        """ how many machines this account may have at once """
        limit = self.user.get("machine_limit")
        return DEFAULT_MACHINE_LIMIT if limit is None else limit


def get_login(client, subject):
    """ the login entity for a subject, or None """
    return client.get(client.key(LOGIN, subject))


def get_user(client, email):
    """ the user entity for an email, or None """
    return client.get(client.key(USER, email))


def load(client, subject):
    """ the Account signed in with subject, or None when there is no such login or it has no user """
    login = get_login(client, subject)
    if login is None or not login.get("user"):
        return None
    user = get_user(client, login["user"])
    return None if user is None else Account(login, user)


def join(client, login):
    """
    Attach a login whose email is proven to the user of that email,
    creating the user on first sight. Returns the user entity.
    """
    email = login["email"]
    with client.transaction():
        user = get_user(client, email)
        if user is None:
            user = datastore.Entity(key=client.key(USER, email), exclude_from_indexes=("picture",))
            user.update({"email": email, "created": now()})
        user["name"] = user.get("name") or login.get("name", "")
        user["picture"] = user.get("picture") or login.get("picture", "")
        user["last_login"] = now()
        login["user"] = email
        client.put(user)
        client.put(login)
    return user


def sign_in(client, subject, email, name, picture, verified, password_hash=None):
    """
    Find or create the login of a sign-in, refresh the profile fields the
    provider owns (and the password hash of an email login, when given),
    and join it to its user when it has none yet. Only a proven email is
    accepted (verified): otherwise UnverifiedEmailError, and nothing is
    stored. Returns the Account.
    """
    email = email.strip().lower()
    if not verified or not email:
        raise UnverifiedEmailError(subject)
    with client.transaction():
        login = get_login(client, subject)
        if login is None:
            login = datastore.Entity(key=client.key(LOGIN, subject), exclude_from_indexes=UNINDEXED)
            login["created"] = now()
        login["email"] = email
        login["name"] = name
        login["picture"] = picture
        if password_hash is not None:
            login["password_hash"] = password_hash
        login["last_login"] = now()
        client.put(login)
    if login.get("user"):
        user = get_user(client, login["user"])
        if user is not None:
            touch(client, login, user)
            return Account(login, user)
    return Account(login, join(client, login))


def touch(client, login, user):
    """ record a sign-in """
    login["last_login"] = now()
    client.put(login)
    user["last_login"] = now()
    client.put(user)


def get_verification(client, email):
    """ the link mailed to an address that has not been followed yet, or None """
    return client.get(client.key(VERIFICATION, email))


def find_verification(client, token_hash):
    """ the pending verification a link's token (by its SHA-256) belongs to, or None """
    query = client.query(kind=VERIFICATION)
    query.add_filter(filter=datastore.query.PropertyFilter("token_hash", "=", token_hash))
    found = list(query.fetch(limit=1))
    return found[0] if found else None


def save_verification(client, email, token_hash):
    """ record that a link was just mailed to the address, voiding the one mailed before """
    pending = datastore.Entity(key=client.key(VERIFICATION, email), exclude_from_indexes=("sent_at",))
    pending.update({"token_hash": token_hash, "sent_at": now()})
    client.put(pending)


def use_verification(client, email, token_hash):
    """
    Spend the link: delete the pending verification of the address if it is
    still the one of this token. Returns whether it was, so of two requests
    with one link only one gets through.
    """
    with client.transaction():
        pending = get_verification(client, email)
        if pending is None or pending.get("token_hash") != token_hash:
            return False
        client.delete(pending.key)
    return True


def assign(client, email, owner, machine_limit=None):
    """
    Set what an admin decides for the user of an email: the machine owner
    label (empty clears it, so the roster decides) and, when given, the
    machine limit (the default clears it). Returns False when there is no
    such user.
    """
    with client.transaction():
        user = get_user(client, email)
        if user is None:
            return False
        if owner:
            user["owner"] = owner
        else:
            user.pop("owner", None)
        if machine_limit is not None:
            if machine_limit == DEFAULT_MACHINE_LIMIT:
                user.pop("machine_limit", None)
            else:
                user["machine_limit"] = machine_limit
        client.put(user)
    return True


# the longest name a user may set (also the name field's maxlength in machines.html)
NAME_MAX_LENGTH = 60


def set_name(client, account, name):
    """ the name a person chose, kept on their user """
    account.user["name"] = name
    client.put(account.user)

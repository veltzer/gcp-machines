"""
Users and their logins, kept in Datastore.

A login (kind "login") is one way of signing in. Its key name is the
provider-qualified subject, the stable identity the provider vouches for:

    google:<sub>      Sign in with Google (the ID token "sub" claim)
    github:<id>       Continue with GitHub (the numeric GitHub user id)
    email:<address>   email and password, registered here
    dev:<address>     the local development login (never on Cloud Run)

A user (kind "user", key name = the lowercased email) is the person: the
same person signing in with Google, GitHub or a password is one user, and
what belongs to them (an admin's machine assignment) lives on the user.

A login joins the user of its email once that email is proven to be the
person's, and never before: Google's email_verified, GitHub's verified
primary address, or for a password login the mailed link followed (see
verification.py). An address typed into the password form proves nothing,
so an unverified password login stays on its own, owning nothing of the
user's; otherwise anyone could register a student's address and walk into
their account. A login joins once and stays: should the provider's address
change later, the login keeps its user rather than moving to another.

Login fields: email (as the provider last reported it), name, picture,
verified, user (the key name of its user; absent until it joins one),
owner and machine_limit (only on a login that has not joined a user: what
an admin set for it; both move to the user on joining), created,
last_login, and for password logins only:

    password_hash        werkzeug hash of the password
    account_id           random, new each time the login is (re)created;
                         a session counts only while it carries the same
                         one, so replacing an unverified login signs out
                         whoever was signed in to the old one
    verification_sent    when the last verification mail went out
    verification_token   the secret in that mail's link; see
                         auth.check_verification

User fields: email, name, picture, owner (the machine owner label an admin
assigned, absent until then), machine_limit (how many machines the user may
have at once, absent for DEFAULT_MACHINE_LIMIT), created, last_login.
"""

import dataclasses
import datetime
import secrets

from google.cloud import datastore

LOGIN = "login"
USER = "user"

# how many machines a user may have at once unless an admin set otherwise,
# and the most an admin may allow
DEFAULT_MACHINE_LIMIT = 1
MAX_MACHINE_LIMIT = 10

# what an admin sets, kept on the user (or on a login with no user yet)
ASSIGNED = ("owner", "machine_limit")

# never indexed: nobody queries by them, and an index on the hash would be a
# second copy of it
UNINDEXED = ("password_hash", "picture", "account_id", "verification_token")


def now():
    return datetime.datetime.now(datetime.UTC)


@dataclasses.dataclass(frozen=True)
class Account:
    """
    Who is signed in: the login they used and the user it belongs to, or
    None while it belongs to none (an unverified password login). The
    Students table also makes one of a user alone, with no login.
    """

    login: datastore.Entity | None
    user: datastore.Entity | None

    @property
    def subject(self):
        return self.login.key.name if self.login is not None else ""

    @property
    def method(self):
        """ "google", "github", "email" or "dev" """
        return self.subject.split(":", 1)[0]

    @property
    def email(self):
        if self.user is not None:
            return self.user.key.name
        return self.login.get("email", "") if self.login is not None else ""

    @property
    def name(self):
        """ what to call this person: the name they set or a provider gave, else their email's local part """
        entity = self.user if self.user is not None else self.login
        name = entity.get("name") if entity is not None else ""
        return name or self.email.split("@")[0]

    @property
    def picture(self):
        """ the photo of the login signed in with, else the user's (from whichever login brought one), else "" """
        for entity in (self.login, self.user):
            if entity is not None and entity.get("picture"):
                return entity["picture"]
        return ""

    @property
    def verified(self):
        """ whether the email is proven to be this person's: exactly when the login has a user """
        return self.user is not None

    @property
    def owner(self):
        """ the machine owner label an admin assigned, or None """
        entity = self.user if self.user is not None else self.login
        return entity.get("owner") if entity is not None else None

    @property
    def machine_limit(self):
        """ how many machines this account may have at once """
        entity = self.user if self.user is not None else self.login
        limit = entity.get("machine_limit") if entity is not None else None
        return DEFAULT_MACHINE_LIMIT if limit is None else limit

    @property
    def target(self):
        """ what an admin's assignment is stored on (see set_owner) """
        return f"user:{self.email}" if self.user is not None else f"login:{self.subject}"

    @property
    def needs_verification(self):
        return self.login is not None and self.method == "email" and not self.login.get("verified")


def get_login(client, subject):
    """ the login entity for a subject, or None """
    return client.get(client.key(LOGIN, subject))


def get_user(client, email):
    """ the user entity for an email, or None """
    return client.get(client.key(USER, email))


def load(client, subject):
    """ the Account signed in with subject, or None when there is no such login """
    login = get_login(client, subject)
    if login is None:
        return None
    user = get_user(client, login["user"]) if login.get("user") else None
    return Account(login, user)


def join(client, login):
    """
    Attach a login whose email is verified to the user of that email,
    creating the user on first sight. What an admin set on the login (its
    assignment, its machine limit) moves to the user, except what the user
    has set already. Returns the user entity.
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
        for field in ASSIGNED:
            value = login.pop(field, None)
            if value is not None and user.get(field) is None:
                user[field] = value
        login["user"] = email
        client.put(user)
        client.put(login)
    return user


def upsert_from_provider(client, subject, email, name, picture, verified):
    """
    Find or create the login of a third-party sign-in, refresh the profile
    fields the provider owns, and join it to its user when its email is
    verified and it has none yet. Returns the Account.
    """
    email = email.strip().lower()
    with client.transaction():
        login = get_login(client, subject)
        if login is None:
            login = datastore.Entity(key=client.key(LOGIN, subject), exclude_from_indexes=UNINDEXED)
            login["created"] = now()
        login["email"] = email
        login["name"] = name
        login["picture"] = picture
        login["verified"] = verified
        login["last_login"] = now()
        client.put(login)
    if login.get("user"):
        user = get_user(client, login["user"])
        if user is not None:
            touch(client, login, user)
            return Account(login, user)
    if verified and email:
        return Account(login, join(client, login))
    return Account(login, None)


def create_password_account(client, email, password_hash):
    """
    Register an email and password login. Returns the new Account, or None
    when the address already has a verified password login.

    An unverified one is replaced: it proves nothing about who owns the
    address, so it must not keep the address from its owner, who may also
    simply have forgotten the password before verifying. Nothing of it
    carries over, an admin's assignment included, except the pace of the
    mails and the secret of the last one. Keeping that secret is safe: it is
    only in the mailbox, and it verifies only a session that also knows the
    new password (see auth.check_verification). It lets the address's owner
    use the mail they already have. An unverified login never joined a
    user, so replacing it takes nothing from anyone.
    """
    subject = f"email:{email}"
    with client.transaction():
        existing = get_login(client, subject)
        if existing is not None and existing.get("verified"):
            return None
        login = datastore.Entity(key=client.key(LOGIN, subject), exclude_from_indexes=UNINDEXED)
        login.update({
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
                    login[field] = existing[field]
        client.put(login)
    return Account(login, None)


def touch(client, login, user=None):
    """ record a sign-in """
    login["last_login"] = now()
    client.put(login)
    if user is not None:
        user["last_login"] = now()
        client.put(user)


def mark_verification_sent(client, login, token):
    """ record when the verification mail went out (which paces resending) and its link's secret """
    login["verification_sent"] = now()
    login["verification_token"] = token
    client.put(login)


def mark_verified(client, login):
    """ the password login's email was shown to be its owner's: it joins that email's user """
    login["verified"] = True
    login.pop("verification_sent", None)
    login.pop("verification_token", None)
    return join(client, login)


def assign(client, target, owner, machine_limit=None):
    """
    Set what an admin decides for "user:<email>" or "login:<subject>" (a
    login with no user yet): the machine owner label (empty clears it, so
    the roster decides) and, when given, the machine limit (the default
    clears it). Returns False when there is no such user or user-less login.
    """
    kind, _, name = target.partition(":")
    if kind not in (USER, LOGIN) or not name:
        return False
    with client.transaction():
        entity = client.get(client.key(kind, name))
        if entity is None or (kind == LOGIN and entity.get("user")):
            return False
        if owner:
            entity["owner"] = owner
        else:
            entity.pop("owner", None)
        if machine_limit is not None:
            if machine_limit == DEFAULT_MACHINE_LIMIT:
                entity.pop("machine_limit", None)
            else:
                entity["machine_limit"] = machine_limit
        client.put(entity)
    return True


# the longest name a user may set (also the name field's maxlength in machines.html)
NAME_MAX_LENGTH = 60


def set_name(client, account, name):
    """ the name a person chose, kept on their user (or on their login while it has none) """
    entity = account.user if account.user is not None else account.login
    entity["name"] = name
    client.put(entity)

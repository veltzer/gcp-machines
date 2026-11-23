"""
Sign-in: Google, GitHub, and email and password, the same three ways as
gcp-myworld (whose src/myworld/auth.py this follows), with the accounts in
Datastore (see users.py) instead of SQL.

Google and GitHub both go through the plain OAuth 2.0 authorization code
flow, so each is one click on its icon: /auth/oauth/<provider> redirects to
the provider with a random state kept in the session, and the callback
checks the state and exchanges the code. From Google that yields a signed
ID token, whose signature and audience google-auth verifies; Google's
callback is /auth/login, the redirect URI registered with the OAuth client.
From GitHub it yields an access token, with which the server asks GitHub
who signed in. A provider is offered only when its client id and secret are
set in the environment.

Only a proven email gets an account: one the provider vouches for (Google's
email_verified, GitHub's verified primary address), or for email sign-in one
whose mailed link was followed. A provider that does not vouch for the email
sends the person back to the sign-in page with an error, and nothing is
stored. An address merely typed into a form proves nothing and can be used
for nothing.

Email sign-up asks for the address only and mails it a link (Identity
Platform sends it, see verification.py); the link leads to /verify, where
its reader chooses the password, which makes the email login with its
werkzeug password hash. So only whoever reads the mailbox ever sets a
password for the address: had sign-up taken one, anyone could register
someone's address with a password of their own and get in once its owner
clicked the link. Asking again for an address that has a login mails a link
the same way, which is how a forgotten password is replaced.

Whichever way was used, the signed Flask session cookie then holds only the
login's subject (its Datastore key name); the login leads to its user, the
person, who is the same whichever way they signed in (see users.py).
"""

import dataclasses
import datetime
import functools
import hashlib
import hmac
import logging
import os
import secrets
import urllib.parse

import flask
import requests
import werkzeug
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from werkzeug.security import check_password_hash, generate_password_hash

from src import users
from src.verification import VerificationError

bp = flask.Blueprint("auth", __name__, url_prefix="/auth")

PASSWORD_MIN_LENGTH = 8
HTTP_TIMEOUT = 10
# how long a mailed link works, and how soon another may be mailed to the same address
LINK_LIFETIME = datetime.timedelta(days=1)
RESEND_INTERVAL = datetime.timedelta(minutes=1)
# what the sign-in page says when the provider does not vouch for the email
UNVERIFIED = "{provider} did not confirm an email address for you: verify one with {provider} and sign in again"

logger = logging.getLogger(__name__)


def _external_url(endpoint, **values):
    # Cloud Run terminates TLS in front of the app, so build the https URL by hand there.
    scheme = "https" if flask.current_app.config["ON_CLOUD_RUN"] else None
    return flask.url_for(endpoint, _external=True, _scheme=scheme, **values)


def datastore_client():
    return flask.current_app.config["DATASTORE"]


def configure(app):
    """
    Read the sign-in settings from the environment, once, into the app config:

    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET   offers Google
    GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET   offers GitHub
    MACHINES_DEV_LOGIN=1                     the local development login,
                                             never on Cloud Run (K_SERVICE)
    """
    app.config["ON_CLOUD_RUN"] = "K_SERVICE" in os.environ
    app.config["DEV_LOGIN"] = os.environ.get("MACHINES_DEV_LOGIN") == "1" and not app.config["ON_CLOUD_RUN"]
    for key in OAUTH_PROVIDERS:
        for part in ("CLIENT_ID", "CLIENT_SECRET"):
            name = f"{key.upper()}_{part}"
            app.config[name] = os.environ.get(name, "")
    app.register_blueprint(bp)


def mailer():
    """ what mails the verification links (verification.IdentityPlatform on Cloud Run, LogMailer locally) """
    return flask.current_app.config["MAILER"]


def dev_login_enabled():
    return flask.current_app.config["DEV_LOGIN"]


def current_user():
    """ the signed-in users.Account, or None """
    if "user" not in flask.g:
        subject = flask.session.get("subject")
        flask.g.user = None if subject is None else users.load(datastore_client(), subject)
    return flask.g.user


def start_session(account):
    flask.session.clear()
    flask.session["subject"] = account.subject
    flask.g.user = account


def login_required(view):
    """ send anyone who is not signed in to the sign-in page """
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return flask.redirect(flask.url_for("root"))
        return view(*args, **kwargs)
    return wrapped


@bp.errorhandler(werkzeug.exceptions.HTTPException)
def handle_http_error(error):
    """ The email form talks JSON (fetch from app.js); the other routes are browser navigations. """
    if flask.request.path.startswith("/auth/email/"):
        return flask.jsonify({"error": error.description}), error.code or 500
    return error.get_response()


# ─── google ──────────────────────────────────────────────────────────────────

def verify_google_token(token):
    """ the verified claims of a Google ID token, raising ValueError otherwise """
    audience = OAUTH_PROVIDERS["google"].client_id
    if not audience:
        raise ValueError("GOOGLE_CLIENT_ID is not configured")
    return id_token.verify_oauth2_token(token, google_requests.Request(), audience)


@bp.route("/login", methods=["GET"])
def google_callback():
    """ where Google returns: the redirect URI registered with the OAuth client """
    return finish_oauth(_provider("google"))


# ─── email and password ──────────────────────────────────────────────────────

def _body():
    body = flask.request.get_json(silent=True)
    if not isinstance(body, dict):
        flask.abort(400, "expected a JSON object")
    return body


def _email(body):
    """ the address in the body, validated for shape only """
    email = body.get("email")
    if not isinstance(email, str):
        flask.abort(400, "an email address is required")
    email = email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@") or " " in email or len(email) > 320:
        flask.abort(400, "that does not look like an email address")
    return email


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _pending(token):
    """ the verification a link's token belongs to, while the link still works; else 400 """
    if not isinstance(token, str) or not token:
        flask.abort(400, "this link is not complete: open the one in the mail")
    pending = users.find_verification(datastore_client(), _token_hash(token))
    if pending is None or users.now() - pending["sent_at"] > LINK_LIFETIME:
        flask.abort(400, "this link was used or has expired: ask for a new one")
    return pending


@bp.route("/email/register", methods=["POST"])
def email_register():
    """
    Mail the address a link to choose a password with. The answer is the
    same whether or not the address has an account, so the form does not
    reveal who signs in here.
    """
    email = _email(_body())
    sent = f"We mailed a link to {email}. Open it to choose your password; until then the address cannot sign in."
    pending = users.get_verification(datastore_client(), email)
    if pending is not None and users.now() - pending["sent_at"] < RESEND_INTERVAL:
        return flask.jsonify({"message": sent})
    # The secret rides in the link's continue URL, so only the mailbox learns it
    token = secrets.token_urlsafe(32)
    try:
        mailer().send(email, _external_url("verify_page", token=token))
    except (VerificationError, requests.RequestException, ValueError) as error:
        logger.warning("verification mail to %s failed: %s", email, error)
        flask.abort(503, "the mail could not be sent: try again in a minute")
    # a new link voids the one mailed before
    users.save_verification(datastore_client(), email, _token_hash(token))
    return flask.jsonify({"message": sent})


@bp.route("/email/verify", methods=["POST"])
def email_verify():
    """ which address a link is for, so its page can say so before asking for the password """
    return flask.jsonify({"email": _pending(_body().get("token")).key.name})


@bp.route("/email/password", methods=["POST"])
def email_password():
    """
    The link's reader chooses the password: the address is proven, so this
    makes (or gives a new password to) its email login, which joins the
    account of the address, and signs in.
    """
    body = _body()
    pending = _pending(body.get("token"))
    password = body.get("password")
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        flask.abort(400, f"use at least {PASSWORD_MIN_LENGTH} characters for the password")
    email = pending.key.name
    # a link works once: of two requests with it only one gets here
    if not users.use_verification(datastore_client(), email, _token_hash(body["token"])):
        flask.abort(400, "this link was used or has expired: ask for a new one")
    account = users.sign_in(
        datastore_client(), f"email:{email}", email, email.split("@")[0], "", verified=True,
        password_hash=generate_password_hash(password),
    )
    try:
        mailer().forget(email)
    except (VerificationError, requests.RequestException, ValueError) as error:
        logger.warning("could not clean up after verifying %s: %s", email, error)
    start_session(account)
    return flask.jsonify({"next": flask.url_for("root")})


@bp.route("/email/login", methods=["POST"])
def email_login():
    body = _body()
    email = _email(body)
    password = body.get("password")
    account = users.load(datastore_client(), f"email:{email}")
    # one message for every failure, so the form does not reveal which emails have accounts
    login = None if account is None else account.login
    hashed = None if login is None else login.get("password_hash")
    if account is None or login is None or not hashed or not isinstance(password, str) \
            or not check_password_hash(hashed, password):
        flask.abort(401, "wrong email or password (a new address signs in only after you open the link we mailed it)")
    users.touch(datastore_client(), login, account.user)
    start_session(account)
    return flask.jsonify({"next": flask.url_for("root")})


# ─── other providers (oauth 2.0 authorization code) ─────────────────────────

@dataclasses.dataclass(frozen=True)
class OAuthProvider:
    key: str
    name: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str
    # the endpoint the provider returns to, when not the generic oauth_callback
    callback: str = "auth.oauth_callback"

    @property
    def client_id(self):
        return flask.current_app.config[f"{self.key.upper()}_CLIENT_ID"]

    @property
    def client_secret(self):
        return flask.current_app.config[f"{self.key.upper()}_CLIENT_SECRET"]

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)


OAUTH_PROVIDERS = {
    "google": OAuthProvider(
        key="google",
        name="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="",
        scope="openid email profile",
        callback="auth.google_callback",
    ),
    "github": OAuthProvider(
        key="github",
        name="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        scope="read:user user:email",
    ),
}


class OAuthError(Exception):
    pass


def _provider(key):
    provider = OAUTH_PROVIDERS.get(key)
    if provider is None or not provider.configured:
        flask.abort(404)
    return provider


def _callback_url(provider):
    if provider.callback == "auth.oauth_callback":
        return _external_url(provider.callback, provider=provider.key)
    return _external_url(provider.callback)


def exchange_code(provider, code, redirect_uri):
    """ trade the authorization code for the provider's token response (a dict) """
    response = requests.post(
        provider.token_url,
        data={
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
        timeout=HTTP_TIMEOUT,
    )
    tokens = response.json() if response.ok else None
    if not isinstance(tokens, dict):
        raise OAuthError(f"{provider.name} did not issue a token")
    return tokens


def fetch_identity(provider, tokens):
    """ who the tokens belong to: (subject, email, name, picture, email verified) """
    if provider.key == "google":
        claims = verify_google_token(tokens.get("id_token") or "")
        return (
            f"google:{claims['sub']}",
            claims.get("email", ""),
            claims.get("name", ""),
            claims.get("picture", ""),
            bool(claims.get("email_verified")),
        )
    token = tokens.get("access_token")
    if not isinstance(token, str) or not token:
        raise OAuthError(f"{provider.name} did not issue an access token")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    response = requests.get(provider.userinfo_url, headers=headers, timeout=HTTP_TIMEOUT)
    if not response.ok:
        raise OAuthError(f"{provider.name} did not tell us who you are")
    info = response.json()
    # Only the verified primary address counts: the profile email is
    # whatever the user typed there and is not necessarily verified.
    email = ""
    emails = requests.get(f"{provider.userinfo_url}/emails", headers=headers, timeout=HTTP_TIMEOUT)
    if emails.ok:
        email = next((e["email"] for e in emails.json() if e.get("primary") and e.get("verified")), "")
    return (
        f"{provider.key}:{info['id']}",
        email,
        info.get("name") or info.get("login") or "",
        info.get("avatar_url") or "",
        bool(email),
    )


@bp.route("/oauth/<provider>", methods=["GET"])
def oauth_start(provider):
    oauth = _provider(provider)
    state = secrets.token_urlsafe(32)
    flask.session["oauth_state"] = state
    params = {
        "client_id": oauth.client_id,
        "redirect_uri": _callback_url(oauth),
        "response_type": "code",
        "scope": oauth.scope,
        "state": state,
    }
    if oauth.key == "google":
        # let someone signed in to several Google accounts pick one
        params["prompt"] = "select_account"
    return flask.redirect(f"{oauth.authorize_url}?{urllib.parse.urlencode(params)}")


def _back_with_error(message):
    return flask.redirect(flask.url_for("root", error=message))


@bp.route("/oauth/<provider>/callback", methods=["GET"])
def oauth_callback(provider):
    oauth = _provider(provider)
    if oauth.callback != "auth.oauth_callback":
        flask.abort(404)
    return finish_oauth(oauth)


def finish_oauth(oauth):
    """ the provider sent the browser back: check the state, learn who it is, sign them in """
    expected = flask.session.pop("oauth_state", "")
    given = flask.request.args.get("state", "")
    if not expected or not hmac.compare_digest(expected, given):
        flask.abort(400, "OAuth state mismatch")
    code = flask.request.args.get("code", "")
    if not code:
        # the user said no on the provider's consent screen
        return _back_with_error(f"{oauth.name} sign-in was cancelled")
    try:
        tokens = exchange_code(oauth, code, _callback_url(oauth))
        subject, email, name, picture, verified = fetch_identity(oauth, tokens)
    except (OAuthError, requests.RequestException, ValueError, KeyError):
        return _back_with_error(f"{oauth.name} sign-in failed, try again")
    try:
        account = users.sign_in(datastore_client(), subject, email, name, picture, verified=verified)
    except users.UnverifiedEmailError:
        return _back_with_error(UNVERIFIED.format(provider=oauth.name))
    start_session(account)
    return flask.redirect(flask.url_for("root"))


# ─── development ─────────────────────────────────────────────────────────────

@bp.route("/dev-login", methods=["POST"])
def dev_login():
    if not dev_login_enabled():
        flask.abort(404)
    email = flask.request.form.get("email", "").strip().lower()
    if not email:
        flask.abort(400, "email is required")
    # local only: the address typed is taken as proven, so the admin list and the roster can be tried out
    account = users.sign_in(datastore_client(), f"dev:{email}", email, email.split("@")[0], "", verified=True)
    start_session(account)
    return flask.redirect(flask.url_for("root"))


@bp.route("/logout", methods=["POST"])
def logout():
    flask.session.clear()
    return flask.redirect(flask.url_for("root"))

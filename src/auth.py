"""
Sign-in: Google, GitHub, and email and password, the same three ways as
gcp-myworld (whose src/myworld/auth.py this follows), with the accounts in
Datastore (see users.py) instead of SQL.

Google uses Google Identity Services in redirect mode: on success Google
POSTs a signed ID token to /auth/login together with a CSRF token that is
also set as a cookie. The server checks the two CSRF copies match and
verifies the token's signature and audience with google-auth.

GitHub goes through the plain OAuth 2.0 authorization code flow:
/auth/oauth/github redirects to GitHub, the callback exchanges the code for
an access token and asks GitHub who signed in. A provider is offered only
when its client id and secret are set in the environment.

Email accounts live entirely in Datastore, with werkzeug password hashes.

Whichever way was used, the signed Flask session cookie then holds only the
account's subject (the Datastore key name).
"""

import dataclasses
import functools
import hmac
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

bp = flask.Blueprint("auth", __name__, url_prefix="/auth")

PASSWORD_MIN_LENGTH = 8
HTTP_TIMEOUT = 10


def datastore_client():
    return flask.current_app.config["DATASTORE"]


def configure(app):
    """
    Read the sign-in settings from the environment, once, into the app config:

    GOOGLE_CLIENT_ID                         offers Sign in with Google
    GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET   offers Continue with GitHub
    MACHINES_DEV_LOGIN=1                     the local development login,
                                             never on Cloud Run (K_SERVICE)
    """
    app.config["ON_CLOUD_RUN"] = "K_SERVICE" in os.environ
    app.config["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID", "")
    app.config["DEV_LOGIN"] = os.environ.get("MACHINES_DEV_LOGIN") == "1" and not app.config["ON_CLOUD_RUN"]
    for key in OAUTH_PROVIDERS:
        for part in ("CLIENT_ID", "CLIENT_SECRET"):
            name = f"{key.upper()}_{part}"
            app.config[name] = os.environ.get(name, "")
    app.register_blueprint(bp)


def google_client_id():
    return flask.current_app.config["GOOGLE_CLIENT_ID"]


def dev_login_enabled():
    return flask.current_app.config["DEV_LOGIN"]


def current_user():
    """ the signed-in user's Datastore entity, or None """
    if "user" not in flask.g:
        subject = flask.session.get("subject")
        flask.g.user = None if subject is None else users.get(datastore_client(), subject)
    return flask.g.user


def start_session(entity):
    flask.session.clear()
    flask.session["subject"] = entity.key.name
    flask.g.user = entity


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
    audience = google_client_id()
    if not audience:
        raise ValueError("GOOGLE_CLIENT_ID is not configured")
    return id_token.verify_oauth2_token(token, google_requests.Request(), audience)


@bp.route("/login", methods=["POST"])
def google_login():
    csrf_cookie = flask.request.cookies.get("g_csrf_token", "")
    csrf_body = flask.request.form.get("g_csrf_token", "")
    if not csrf_cookie or not hmac.compare_digest(csrf_cookie, csrf_body):
        flask.abort(400, "CSRF token mismatch")
    try:
        claims = verify_google_token(flask.request.form.get("credential", ""))
    except ValueError:
        flask.abort(401, "Invalid Google sign-in token")
    entity = users.upsert_from_provider(
        datastore_client(),
        f"google:{claims['sub']}",
        claims.get("email", ""),
        claims.get("name", ""),
        claims.get("picture", ""),
        verified=bool(claims.get("email_verified")),
    )
    start_session(entity)
    return flask.redirect(flask.url_for("root"))


# ─── email and password ──────────────────────────────────────────────────────

def _email_credentials():
    """ the (email, password) pair from the JSON body, validated for shape only """
    body = flask.request.get_json(silent=True)
    if not isinstance(body, dict):
        flask.abort(400, "expected a JSON object")
    email = body.get("email")
    password = body.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        flask.abort(400, "email and password are required")
    email = email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@") or " " in email:
        flask.abort(400, "that does not look like an email address")
    return email, password


@bp.route("/email/register", methods=["POST"])
def email_register():
    email, password = _email_credentials()
    if len(password) < PASSWORD_MIN_LENGTH:
        flask.abort(400, f"use at least {PASSWORD_MIN_LENGTH} characters for the password")
    entity = users.create_password_account(datastore_client(), email, generate_password_hash(password))
    if entity is None:
        flask.abort(409, "there is already an account with this email, sign in instead")
    start_session(entity)
    return flask.jsonify({"next": flask.url_for("root")})


@bp.route("/email/login", methods=["POST"])
def email_login():
    email, password = _email_credentials()
    entity = users.get(datastore_client(), f"email:{email}")
    # one message for both failures, so the form does not reveal which emails have accounts
    hashed = None if entity is None else entity.get("password_hash")
    if not hashed or not check_password_hash(hashed, password):
        flask.abort(401, "wrong email or password")
    users.touch(datastore_client(), entity)
    start_session(entity)
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
    # Cloud Run terminates TLS in front of the app, so build the https URL by hand there.
    scheme = "https" if flask.current_app.config["ON_CLOUD_RUN"] else None
    return flask.url_for("auth.oauth_callback", provider=provider.key, _external=True, _scheme=scheme)


def exchange_code(provider, code, redirect_uri):
    """ trade the authorization code for an access token """
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
    token = response.json().get("access_token") if response.ok else None
    if not isinstance(token, str) or not token:
        raise OAuthError(f"{provider.name} did not issue an access token")
    return token


def fetch_identity(provider, token):
    """ ask the provider who the token belongs to: (subject, email, name, picture) """
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
    return flask.redirect(f"{oauth.authorize_url}?{urllib.parse.urlencode(params)}")


def _back_with_error(message):
    return flask.redirect(flask.url_for("root", error=message))


@bp.route("/oauth/<provider>/callback", methods=["GET"])
def oauth_callback(provider):
    oauth = _provider(provider)
    expected = flask.session.pop("oauth_state", "")
    given = flask.request.args.get("state", "")
    if not expected or not hmac.compare_digest(expected, given):
        flask.abort(400, "OAuth state mismatch")
    code = flask.request.args.get("code", "")
    if not code:
        # the user said no on the provider's consent screen
        return _back_with_error(f"{oauth.name} sign-in was cancelled")
    try:
        token = exchange_code(oauth, code, _callback_url(oauth))
        subject, email, name, picture = fetch_identity(oauth, token)
    except (OAuthError, requests.RequestException, ValueError, KeyError):
        return _back_with_error(f"{oauth.name} sign-in failed, try again")
    entity = users.upsert_from_provider(datastore_client(), subject, email, name, picture, verified=bool(email))
    start_session(entity)
    return flask.redirect(flask.url_for("root"))


# ─── development ─────────────────────────────────────────────────────────────

@bp.route("/dev-login", methods=["POST"])
def dev_login():
    if not dev_login_enabled():
        flask.abort(404)
    email = flask.request.form.get("email", "").strip().lower()
    if not email:
        flask.abort(400, "email is required")
    # verified, so the admin list and the roster can be tried out locally
    entity = users.upsert_from_provider(datastore_client(), f"dev:{email}", email, email.split("@")[0], "", verified=True)
    start_session(entity)
    return flask.redirect(flask.url_for("root"))


@bp.route("/logout", methods=["POST"])
def logout():
    flask.session.clear()
    return flask.redirect(flask.url_for("root"))

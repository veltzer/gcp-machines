#!/usr/bin/env python

"""
main application
"""

import os
import secrets
import time
import urllib.parse
import warnings

import flask
import google.auth
import markupsafe
from google.cloud import datastore
from googleapiclient import discovery
from googleapiclient.errors import HttpError

from src import auth

HTML_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "html")


def secret_key():
    """ the key that signs the session cookie: required on Cloud Run, random locally """
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    if "K_SERVICE" in os.environ:
        raise RuntimeError("SECRET_KEY must be set on Cloud Run (see gcp_secrets in .gcp.conf)")
    warnings.warn("SECRET_KEY not set: sessions will not survive a restart", stacklevel=1)
    return secrets.token_hex(32)


app = flask.Flask(__name__)
app.config["SECRET_KEY"] = secret_key()
app.config["SESSION_COOKIE_HTTPONLY"] = True
# Lax keeps the cookie off cross-site POSTs, which is what protects the
# state-changing forms (/process, /auth/logout) from other sites
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = "K_SERVICE" in os.environ
# The hostname the service is meant to be reached on; empty in development.
app.config["CANONICAL_HOST"] = os.environ.get("CANONICAL_HOST", "")
auth.configure(app)


credentials, project_id = google.auth.default()
compute = discovery.build("compute", "v1", credentials=credentials)
datastore_client = datastore.Client(project=project_id)
app.config["DATASTORE"] = datastore_client

# Admins see and control every machine; everyone else only their own. Comma
# separated emails, set in .gcp.conf. Only a verified email counts (a Google
# or GitHub sign-in), never one typed into the password form.
ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.environ.get("ADMIN_EMAILS", "").split(",")
    if email.strip()
}

# The course roster, email -> machine owner label, lives in Datastore (kind
# "student", key = email), pushed there by `scripts/iap.py sync`. It is
# cached briefly so roster changes show up without a redeploy while not
# querying Datastore on every request.
MAPPING_TTL_SECONDS = 60
_mapping_cache = {"expiry": 0.0, "mapping": {}}


def email_to_owner():
    """ email -> owner mapping from Datastore, cached for a short while """
    now = time.monotonic()
    if now >= _mapping_cache["expiry"]:
        try:
            _mapping_cache["mapping"] = {
                entity.key.name: entity["owner"]
                for entity in datastore_client.query(kind="student").fetch()
            }
        except Exception:  # noqa: BLE001,S110 # pylint: disable=broad-exception-caught
            # keep serving the last known mapping over a Datastore hiccup
            pass
        _mapping_cache["expiry"] = now + MAPPING_TTL_SECONDS
    return _mapping_cache["mapping"]


def is_admin(user):
    """ admins see and control all machines """
    return bool(user.get("verified")) and user.get("email", "").lower() in ADMIN_EMAILS


def owner_of(user):
    """
    The machine owner label this account may control, or None. An owner an
    admin assigned to the account wins; otherwise a verified email is looked
    up in the roster. An unverified (password) account is never matched by
    email, or anyone could register a student's address and take over their
    machine.
    """
    if user.get("owner"):
        return user["owner"]
    if user.get("verified") and user.get("email"):
        return email_to_owner().get(user["email"].lower())
    return None


@app.before_request
def redirect_to_canonical_host():
    """
    Cloud Run also answers on its run.app hostnames. Send those to the custom
    domain first, so there is one origin for the session cookie and the
    sign-in providers only ever see the callback URL registered with them.
    The health check is exempt so probes addressing the service keep working.
    """
    host = app.config["CANONICAL_HOST"]
    if not host or flask.request.host == host or flask.request.path == "/app/health":
        return None
    url = urllib.parse.urlsplit(flask.request.url)._replace(scheme="https", netloc=host).geturl()
    return flask.redirect(url, code=308)


@app.route("/app/health", methods=["GET"])
def health():
    return "ok"


@app.route("/api/config", methods=["GET"])
def api_config():
    """ what the sign-in page needs: which ways in this server offers """
    return flask.jsonify({
        "signed_in": auth.current_user() is not None,
        "google_client_id": auth.google_client_id(),
        "dev_login": auth.dev_login_enabled(),
        "password_min_length": auth.PASSWORD_MIN_LENGTH,
        # every provider the page knows about, so it can grey out the ones this server cannot offer
        "providers": {key: p.configured for key, p in auth.OAUTH_PROVIDERS.items()},
    })


def get_machines():
    """ list all instances in all zones """
    # pylint: disable=no-member
    request = compute.instances().aggregatedList(project=project_id)
    all_instances = []
    while request is not None:
        response = request.execute()
        for instances_data in response["items"].values():
            all_instances.extend(instances_data.get("instances", []))
        request = compute.instances().aggregatedList_next(
                previous_request=request,
                previous_response=response
        )
    data = []
    for number, instance in enumerate(all_instances):
        interfaces = instance.get("networkInterfaces", [])
        access_configs = interfaces[0].get("accessConfigs", []) if interfaces else []
        # GCP reports a stopped machine as TERMINATED, which reads as if the
        # machine is gone; show the friendlier name
        status = instance["status"]
        data.append({
            "name": instance["name"],
            "status": "STOPPED" if status == "TERMINATED" else status,
            "owner": instance.get("labels", {}).get("owner", "unknown"),
            "ip": access_configs[0].get("natIP", "N/A") if access_configs else "N/A",
            "zone": instance["zone"].split("/")[-1],
            "number": number,
        })
    return data


@app.route("/", methods=["GET"])
def root():
    """ the sign-in page, or the machines the signed-in user may control """
    user = auth.current_user()
    if user is None:
        return flask.send_from_directory(HTML_DIR, "index.html")
    machines = get_machines()
    admin = is_admin(user)
    if not admin:
        owner = owner_of(user)
        machines = [m for m in machines if owner is not None and m["owner"] == owner]
    return flask.render_template("machines.html", machines=machines, user=user, admin=admin)


@app.route("/process", methods=["POST"])
@auth.login_required
def process():
    """ toggle the state of a single machine """
    # pylint: disable=no-member
    name = flask.request.form["name"]
    zone = flask.request.form["zone"]
    # decide what to do from the live status, not from whatever the browser
    # showed when the page was last rendered
    instance = compute.instances().get(project=project_id, zone=zone, instance=name).execute()
    user = auth.current_user()
    if not is_admin(user):
        owner = instance.get("labels", {}).get("owner")
        if owner is None or owner_of(user) != owner:
            flask.abort(403, "This machine is not yours.")
    status = instance["status"]
    if status == "SUSPENDED":
        compute.instances().resume(project=project_id, zone=zone, instance=name).execute()
        action = "resume"
    elif status == "TERMINATED":
        compute.instances().start(project=project_id, zone=zone, instance=name).execute()
        action = "start"
    elif status == "RUNNING":
        compute.instances().suspend(project=project_id, zone=zone, instance=name).execute()
        action = "suspend"
    else:
        return (
            (
                f"Machine {markupsafe.escape(name)} is currently {markupsafe.escape(status)}, "
                "which means it is already in the middle of a state change."
                "<br/>Go back, refresh and try again in a minute."
            ),
            409,
        )
    return (
        f"Requested {action} of machine {markupsafe.escape(name)}. "
        "Usually it takes 1-2 minutes to really happen."
        "<br/>If you started a machine go back and refresh until you get an IP to connect to. "
        "Use the same key as before."
    )


@app.errorhandler(HttpError)
def handle_gcp_error(error):
    """ show google api failures as a readable message instead of a stack trace """
    return (
        (
            f"Google Cloud API call failed: {markupsafe.escape(error.reason)}"
            "<br/>Go back, refresh and try again."
        ),
        502,
    )


if __name__ == "__main__":
    # local development only: the debugger runs code, so never off this machine
    app.run(debug=True, host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))

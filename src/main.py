#!/usr/bin/env python

"""
main application
"""

import base64
import datetime
import os
import re
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

from src import auth, users, verification

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
secret_manager = discovery.build("secretmanager", "v1", credentials=credentials)

# Each machine's private ssh key lives in Secret Manager under this prefix
# plus the instance name, put there by `scripts/machines.py create` (keep in
# sync with KEY_SECRET_PREFIX there).
KEY_SECRET_PREFIX = "machine-key-"

# What Compute Engine allows in instance and zone names; checked before a
# name goes into an API path.
RESOURCE_NAME = re.compile(r"[a-z]([-a-z0-9]{0,61}[a-z0-9])?")
datastore_client = datastore.Client(project=project_id)
app.config["DATASTORE"] = datastore_client
app.config["VERIFIER"] = verification.IdentityPlatform(credentials, project_id)

# Admins see and control every machine; everyone else only their own. Comma
# separated emails, set in .gcp.conf. Only a verified email counts (a Google
# or GitHub sign-in, or a password account whose mailed link was followed).
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


def assignment(user):
    """
    The machine owner label this account may control and where that comes
    from: ("<owner>", "assigned") when an admin assigned it on the Students
    table, ("<owner>", "roster") when the account's verified email is in the
    course roster, (None, None) otherwise. An unverified password account
    is never matched by email, or anyone could register a student's address
    and take over their machine.
    """
    if user.get("owner"):
        return user["owner"], "assigned"
    if user.get("verified") and user.get("email"):
        owner = email_to_owner().get(user["email"].lower())
        if owner is not None:
            return owner, "roster"
    return None, None


def owner_of(user):
    """ the machine owner label this account may control, or None """
    return assignment(user)[0]


# how each sign-in method is named on the Students table, by subject prefix
METHODS = {"google": "Google", "github": "GitHub", "email": "Password", "dev": "Development"}


def students_table(machines):
    """
    Every account and its machine, then the roster students who have not
    signed up yet (or only with a password, which the roster does not
    match): what the admin sees on the Students table. Also annotates each
    machine with the emails it is assigned to.
    """
    rows = []
    signed_up = set()
    for user in datastore_client.query(kind="user").fetch():
        subject = user.key.name
        owner, source = assignment(user)
        if user.get("verified"):
            signed_up.add(user.get("email", "").lower())
        # The roster's machine for an account whose email it cannot trust
        # (a password account): offered to the admin to confirm, never given.
        suggested = ""
        if owner is None and not user.get("verified"):
            suggested = email_to_owner().get(user.get("email", "").lower(), "")
        method = METHODS.get(subject.split(":", 1)[0], subject.split(":", 1)[0])
        if auth.needs_verification(user):
            method += " (email not verified yet)"
        rows.append({
            "subject": subject,
            "email": user.get("email", ""),
            "method": method,
            "admin": is_admin(user),
            "owner": owner,
            "source": source,
            "assigned": user.get("owner") or "",
            "suggested": suggested,
        })
    rows.sort(key=lambda row: (row["email"], row["method"]))
    pending = [
        {"email": email, "owner": owner}
        for email, owner in sorted(email_to_owner().items())
        if email not in signed_up
    ]
    for machine in machines:
        machine["students"] = sorted(
            {row["email"] for row in rows if row["owner"] == machine["owner"]}
            | {row["email"] for row in pending if row["owner"] == machine["owner"]}
        )
    return rows, pending


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
            "turns_off_at": turns_off_at(instance),
        })
    return data


def turns_off_at(instance):
    """
    When a running machine will turn itself off (UTC, ISO 8601), or None.
    Compute Engine stops a machine maxRunDuration after it last entered
    RUNNING (scripts/machines.py sets 4 hours); a start and a resume both
    move lastStartTimestamp, which is what the countdown starts from.
    """
    seconds = instance.get("scheduling", {}).get("maxRunDuration", {}).get("seconds")
    started = instance.get("lastStartTimestamp")
    if instance.get("status") != "RUNNING" or not seconds or not started:
        return None
    end = datetime.datetime.fromisoformat(started) + datetime.timedelta(seconds=int(seconds))
    return end.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.route("/", methods=["GET"])
def root():
    """ the sign-in page, or the machines the signed-in user may control """
    user = auth.current_user()
    if user is None:
        return flask.send_from_directory(HTML_DIR, "index.html")
    auth.check_verification(user)
    machines = get_machines()
    admin = is_admin(user)
    students = pending = owners = None
    if admin:
        students, pending = students_table(machines)
        owners = sorted({m["owner"] for m in machines if m["owner"] != "unknown"})
    else:
        owner = owner_of(user)
        machines = [m for m in machines if owner is not None and m["owner"] == owner]
    return flask.render_template(
        "machines.html",
        machines=machines,
        user=user,
        admin=admin,
        unverified=auth.needs_verification(user),
        students=students,
        pending=pending,
        owners=owners,
    )


def get_instance_for(user, name, zone):
    """
    The live instance, if the user may control it: an admin any, a student
    only one whose owner label is their assignment. Aborts otherwise.
    """
    # pylint: disable=no-member
    if not RESOURCE_NAME.fullmatch(name) or not RESOURCE_NAME.fullmatch(zone):
        flask.abort(400, "Bad machine name or zone.")
    instance = compute.instances().get(project=project_id, zone=zone, instance=name).execute()
    if not is_admin(user):
        owner = instance.get("labels", {}).get("owner")
        if owner is None or owner_of(user) != owner:
            flask.abort(403, "This machine is not yours.")
    return instance


@app.route("/key", methods=["GET"])
@auth.login_required
def download_key():
    """ the machine's private ssh key, as a file, for its student or an admin """
    # pylint: disable=no-member
    name = flask.request.args["name"]
    get_instance_for(auth.current_user(), name, flask.request.args["zone"])
    secret = f"projects/{project_id}/secrets/{KEY_SECRET_PREFIX}{name}/versions/latest"
    try:
        version = secret_manager.projects().secrets().versions().access(name=secret).execute()
    except HttpError as error:
        if error.resp.status == 404:
            flask.abort(404, "This machine has no downloadable key. Ask your instructor.")
        raise
    response = flask.Response(base64.b64decode(version["payload"]["data"]), mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = f'attachment; filename="{name}.key"'
    # a private key: never kept by a browser cache or a proxy
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/admin/assign", methods=["POST"])
@auth.login_required
def assign():
    """
    Assign a machine (by its owner label) to an account, or clear the
    assignment so the account falls back to the roster. Admins only.
    """
    if not is_admin(auth.current_user()):
        flask.abort(403, "Only an admin may assign machines.")
    subject = flask.request.form["subject"]
    owner = flask.request.form["owner"]
    if owner and owner not in {m["owner"] for m in get_machines()}:
        flask.abort(400, "There is no machine with that owner.")
    if not users.set_owner(datastore_client, subject, owner):
        flask.abort(404, "There is no such account.")
    return flask.redirect(flask.url_for("root", _anchor="students"))


@app.route("/process", methods=["POST"])
@auth.login_required
def process():
    """ toggle the state of a single machine """
    # pylint: disable=no-member
    name = flask.request.form["name"]
    zone = flask.request.form["zone"]
    # decide what to do from the live status, not from whatever the browser
    # showed when the page was last rendered
    instance = get_instance_for(auth.current_user(), name, zone)
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
        "It turns itself off 4 hours after it starts; your files are kept. "
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

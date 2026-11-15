#!/usr/bin/env python

"""
main application
"""

import base64
import datetime
import json
import os
import secrets
import threading
import time
import urllib.parse
import warnings

import flask
import google.auth
import google_auth_httplib2
import httplib2
import markupsafe
from google.cloud import datastore
from googleapiclient import discovery
from googleapiclient.errors import HttpError

from src import auth, provision, users, verification

HTML_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "html")

# The deploy stamp gcloud_deploy.sh (utils-bash) writes at the repo (and
# image) root just before deploying: when, and from which commit. Absent in
# development.
BUILD_INFO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build_info.json")


def load_build_info():
    """ the deploy stamp, or placeholders when there is none (development) """
    try:
        with open(BUILD_INFO, encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {"deploy_date": "unknown", "git_describe": "dev"}


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
app.config["BUILD_INFO"] = load_build_info()
auth.configure(app)


credentials, project_id = google.auth.default()
compute = discovery.build("compute", "v1", credentials=credentials)
secret_manager = discovery.build("secretmanager", "v1", credentials=credentials)

# The service objects above may be shared, but the httplib2 connection they
# carry is not thread-safe, and gunicorn serves requests on several threads.
# So every request passes its thread's own connection: .execute(http=api_http()).
_thread_local = threading.local()


def api_http():
    """ this thread's own authorized connection for the Google API clients """
    http = getattr(_thread_local, "http", None)
    if http is None:
        http = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http())
        _thread_local.http = http
    return http

# What Compute Engine allows in instance and zone names; checked before a
# name goes into an API path.
RESOURCE_NAME = provision.RESOURCE_NAME
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
    """ admins see and control all machines; only a verified email counts """
    return user.verified and user.email.lower() in ADMIN_EMAILS


def assignment(user):
    """
    The machine owner label this account may control and where that comes
    from: ("<owner>", "assigned") when an admin assigned it on the Students
    table, ("<owner>", "roster") when the user's email is in the course
    roster, (None, None) otherwise. Only a user is matched by email: a login
    with none yet (an unverified password login) proves nothing about the
    address, or anyone could register a student's address and take over
    their machine.
    """
    if user.owner:
        return user.owner, "assigned"
    if user.verified:
        owner = email_to_owner().get(user.email.lower())
        if owner is not None:
            return owner, "roster"
    return None, None


def owner_of(user):
    """ the machine owner label this account may control, or None """
    return assignment(user)[0]


# how each sign-in method is named on the Students table, by subject prefix
METHODS = {"google": "Google", "github": "GitHub", "email": "Password", "dev": "Development"}


# the icon the top bar shows for each sign-in method (static/<icon>.svg,
# picked in machines.html); a development login is an email typed in, so it
# gets the email icon
METHOD_ICONS = {"google": "google", "github": "github", "email": "email", "dev": "email"}


def sign_in_method(user):
    """ how this account signed in, for the top bar: {"name", "icon"} """
    name = "email and password" if user.method == "email" else METHODS.get(user.method, user.method)
    return {"name": name, "icon": METHOD_ICONS.get(user.method, "email")}


# what each provider's own id for a person is called, in the account panel
PROVIDER_IDS = {"google": "Google ID", "github": "GitHub user ID", "email": "Email", "dev": "Email"}


def describe_login(login, current, email):
    """ one way of signing in, as the account panel shows it """
    method, _, provider_id = login.key.name.partition(":")
    last = login.get("last_login")
    return {
        "name": "Email and password" if method == "email" else METHODS.get(method, method),
        "icon": METHOD_ICONS.get(method, "email"),
        "id_label": PROVIDER_IDS.get(method, "ID"),
        "id": provider_id,
        # what the provider reports, only when it is news: not the account's
        # email again, nor an email login's id (which is its email)
        "email": "" if login.get("email", "") in (provider_id, email) else login.get("email", ""),
        "last": last.strftime("%Y-%m-%d") if last else "",
        "current": login.key.name == current,
        "unverified": method == "email" and not login.get("verified"),
    }


def account_details(user, admin):
    """
    What the account panel under the top bar's name shows: who is signed in,
    in which role, which machine they get and why, and every way this user
    signs in (only the one used, for a login with no user yet).
    """
    if user.user is not None:
        query = datastore_client.query(kind=users.LOGIN)
        query.add_filter(filter=datastore.query.PropertyFilter("user", "=", user.email))
        logins = list(query.fetch())
    else:
        logins = [user.login]
    # the one in use first, then the others by name
    described = sorted(
        (describe_login(login, user.subject, user.email) for login in logins),
        key=lambda login: (not login["current"], login["name"]),
    )
    owner, source = assignment(user)
    created = (user.user or user.login).get("created")
    return {
        "email": user.email,
        "role": "Admin" if admin else "Student",
        "machine": f"{owner} ({'assigned by an admin' if source == 'assigned' else 'from the roster'})" if owner else "none",
        "since": created.strftime("%Y-%m-%d") if created else "",
        "limit": user.machine_limit,
        "logins": described,
    }


def students_table(machines):
    """
    Every user (with all the ways they sign in) and every login that has no
    user yet (an unverified password login), then the roster students who
    have not signed up: what the admin sees on the Students table. Also
    annotates each machine with the emails it is assigned to.
    """
    logins = list(datastore_client.query(kind=users.LOGIN).fetch())
    methods = {}
    for login in logins:
        if login.get("user"):
            methods.setdefault(login["user"], []).append(login.key.name.split(":", 1)[0])
    accounts = [users.Account(None, user) for user in datastore_client.query(kind=users.USER).fetch()]
    accounts += [users.Account(login, None) for login in logins if not login.get("user")]
    rows = []
    for account in accounts:
        owner, source = assignment(account)
        if account.verified:
            names = sorted(METHODS.get(m, m) for m in methods.get(account.email, []))
            method = ", ".join(names) or "none"
            suggested = ""
        else:
            method = METHODS.get(account.method, account.method)
            if account.needs_verification:
                method += " (email not verified yet)"
            # the roster's machine for an address it cannot trust yet:
            # offered to the admin to confirm, never given
            suggested = "" if owner else email_to_owner().get(account.email.lower(), "")
        rows.append({
            "target": account.target,
            "email": account.email,
            "method": method,
            "admin": is_admin(account),
            "owner": owner,
            "source": source,
            "assigned": account.owner or "",
            "suggested": suggested,
            "limit": account.machine_limit,
        })
    rows.sort(key=lambda row: (row["email"], row["method"]))
    signed_up = {row["email"].lower() for row in rows if row["target"].startswith("user:")}
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


# What a page may load: only this origin, plus the Google Identity Services
# script, button frame and stylesheet the sign-in page uses (the sources
# Google documents for GIS). frame-ancestors keeps other sites from framing
# the Start/Suspend, Download key and Assign buttons (clickjacking).
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' https://accounts.google.com/gsi/client; "
    "style-src 'self' https://accounts.google.com/gsi/style; "
    "frame-src https://accounts.google.com/gsi/; "
    "connect-src 'self' https://accounts.google.com/gsi/; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


@app.after_request
def security_headers(response):
    """
    Headers every response carries. The CSP goes on HTML pages only: an SVG
    under /static has its own inline <style>, which the policy would block.
    """
    headers = response.headers
    if response.mimetype == "text/html":
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("X-Content-Type-Options", "nosniff")
    # GIS needs the origin in the referrer; nothing needs more than that cross-site
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if "K_SERVICE" in os.environ:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.route("/app/health", methods=["GET"])
def health():
    return "ok"


@app.route("/app/version", methods=["GET"])
def version():
    """ what is serving: the deploy stamp plus the Cloud Run revision """
    info = dict(app.config["BUILD_INFO"])
    # Cloud Run injects the serving revision name at runtime
    info["revision"] = os.environ.get("K_REVISION", "local")
    return flask.jsonify(info)


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
        response = request.execute(http=api_http())
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
    RUNNING (provision.MAX_RUN_SECONDS); a start and a resume both
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
    user = auth.check_verification(user)
    machines = get_machines()
    admin = is_admin(user)
    students = pending = owners = summary = None
    owner = owner_of(user)
    # how many of its machines the account has, and whether it may make another
    count = sum(m["owner"] == owner for m in machines) if owner is not None else 0
    can_create = owner is not None and count < user.machine_limit
    if admin:
        students, pending = students_table(machines)
        owners = known_owners(machines)
        summary = machines_summary(machines)
    else:
        machines = [m for m in machines if owner is not None and m["owner"] == owner]
    return flask.render_template(
        "machines.html",
        machines=machines,
        user=user,
        admin=admin,
        unverified=user.needs_verification,
        sign_in=sign_in_method(user),
        account=account_details(user, admin),
        students=students,
        pending=pending,
        owners=owners,
        owner=owner,
        can_create=can_create,
        count=count,
        limit=user.machine_limit,
        max_limit=users.MAX_MACHINE_LIMIT,
        summary=summary,
        hours=provision.MAX_RUN_SECONDS // 3600,
    )


def known_owners(machines):
    """ the owner labels an admin may assign: the roster's and those of existing machines """
    return sorted(set(email_to_owner().values()) | {m["owner"] for m in machines if m["owner"] != "unknown"})


def machines_summary(machines):
    """
    What the admin sees above the machines: how many there are, how many
    are up (running, or on their way up) and stopped, and how many roster
    students have one.
    """
    up = {"PROVISIONING", "STAGING", "RUNNING"}
    roster = set(email_to_owner().values())
    return {
        "total": len(machines),
        "up": sum(m["status"] in up for m in machines),
        "stopped": sum(m["status"] in {"STOPPED", "SUSPENDED"} for m in machines),
        "students_with": len(roster & {m["owner"] for m in machines}),
        "students": len(roster),
    }


def get_instance_for(user, name, zone):
    """
    The live instance, if the user may control it: an admin any, a student
    only one whose owner label is their assignment. Aborts otherwise.
    """
    # pylint: disable=no-member
    if not RESOURCE_NAME.fullmatch(name) or not RESOURCE_NAME.fullmatch(zone):
        flask.abort(400, "Bad machine name or zone.")
    instance = compute.instances().get(project=project_id, zone=zone, instance=name).execute(http=api_http())
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
    secret = f"projects/{project_id}/secrets/{provision.KEY_SECRET_PREFIX}{name}/versions/latest"
    try:
        version = secret_manager.projects().secrets().versions().access(name=secret).execute(http=api_http())
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
    Set, for a user or a login that has no user yet, which owner label its
    machines carry (empty: the roster decides) and how many machines it may
    have at once. Admins only.
    """
    if not is_admin(auth.current_user()):
        flask.abort(403, "Only an admin may assign machines.")
    target = flask.request.form["target"]
    owner = flask.request.form["owner"]
    if owner and owner not in known_owners(get_machines()):
        flask.abort(400, "There is no student or machine with that owner.")
    limit = flask.request.form.get("limit")
    if limit is not None:
        if not limit.isdigit() or int(limit) > users.MAX_MACHINE_LIMIT:
            flask.abort(400, f"The machine limit is a number from 0 to {users.MAX_MACHINE_LIMIT}.")
        limit = int(limit)
    if not users.assign(datastore_client, target, owner, limit):
        flask.abort(404, "There is no such account.")
    return flask.redirect(flask.url_for("root", _anchor="students"))


def done(message):
    """ back to the machines page, which shows the message on top """
    flask.flash(message)
    return flask.redirect(flask.url_for("root"))


def instance_from_form():
    """
    The machine the posted form names, if the signed-in account may control
    it: (name, zone, live instance).
    """
    name, zone = flask.request.form["name"], flask.request.form["zone"]
    return name, zone, get_instance_for(auth.current_user(), name, zone)


@app.route("/create", methods=["POST"])
@auth.login_required
def create():
    """
    Create a machine for the signed-in account, under the owner label its
    assignment names, while it has fewer than its machine limit. It gets
    the lowest machine number the owner does not use yet.
    """
    user = auth.current_user()
    owner = owner_of(user)
    if owner is None:
        flask.abort(403, "No machine is assigned to you. Contact your instructor.")
    names = {m["name"] for m in get_machines() if m["owner"] == owner}
    if len(names) >= user.machine_limit:
        limit = user.machine_limit
        return done(f"You have {len(names)} machine{'' if len(names) == 1 else 's'}, "
                    f"as many as you may have ({limit}). Ask your instructor for more.")
    number = provision.next_number(owner, names)
    if provision.instance_name(owner, number) is None:
        flask.abort(400, "Your assignment does not make a valid machine name. Contact your instructor.")
    zone = provision.pick_zone(compute, project_id, http=api_http())
    if zone is None:
        return done("There is no room for another running machine right now. Try again later.")
    try:
        name, _operation = provision.create(compute, secret_manager, project_id, owner, number, zone, http=api_http())
    except HttpError as error:
        if error.resp.status == 409:  # a second click, or one of that name still being deleted
            return done("That machine is already being created or deleted. Refresh in a minute.")
        raise
    return done(
        f"Creating machine {name}. It takes a minute or two: refresh until it has an IP, "
        "then download its key."
    )


@app.route("/start", methods=["POST"])
@auth.login_required
def start():
    """ start a stopped machine, or resume a suspended one """
    # pylint: disable=no-member
    name, zone, instance = instance_from_form()
    if instance["status"] == "TERMINATED":
        compute.instances().start(project=project_id, zone=zone, instance=name).execute(http=api_http())
    elif instance["status"] == "SUSPENDED":
        compute.instances().resume(project=project_id, zone=zone, instance=name).execute(http=api_http())
    else:
        return done(busy(name, instance))
    return done(
        f"Starting machine {name}. Refresh in a minute or two until it has an IP. "
        f"It turns itself off {provision.MAX_RUN_SECONDS // 3600} hours after it starts; your files are kept."
    )


@app.route("/stop", methods=["POST"])
@auth.login_required
def stop():
    """ stop a running machine: its disk stays, what runs in memory does not """
    # pylint: disable=no-member
    name, zone, instance = instance_from_form()
    if instance["status"] != "RUNNING":
        return done(busy(name, instance))
    compute.instances().stop(project=project_id, zone=zone, instance=name).execute(http=api_http())
    return done(f"Stopping machine {name}. Your files are kept; start it again when you need it.")


@app.route("/delete", methods=["POST"])
@auth.login_required
def delete():
    """ delete a machine, its disk and its key, for good """
    # pylint: disable=no-member
    name, zone, _instance = instance_from_form()
    compute.instances().delete(project=project_id, zone=zone, instance=name).execute(http=api_http())
    provision.delete_key(secret_manager, project_id, name, http=api_http())
    return done(f"Deleting machine {name} and everything on it.")


def busy(name, instance):
    """ what to say when the machine is not in a state the action applies to """
    status = "STOPPED" if instance["status"] == "TERMINATED" else instance["status"]
    return f"Machine {name} is {status} right now. Refresh and try again in a minute."


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

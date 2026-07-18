#!/usr/bin/env python

"""
main application
"""

import hmac
import os

import flask
import markupsafe
import google.auth
from googleapiclient import discovery
from googleapiclient.errors import HttpError


app = flask.Flask(__name__)


credentials, project_id = google.auth.default()
compute = discovery.build("compute", "v1", credentials=credentials)

# Optional shared secret: when the ACCESS_TOKEN environment variable is set
# (see app.yaml), every request must present it as ?token=<value> once and it
# is then remembered in a cookie. The real protection is Identity-Aware Proxy
# (see doc/iap.md); once IAP is enabled this token is redundant and can stay
# unset.
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN")


def get_signed_in_user():
    """ email of the user IAP authenticated, or None when IAP is off """
    # IAP sends "accounts.google.com:user@example.com"; the header cannot be
    # spoofed from outside because IAP strips it from incoming requests
    header = flask.request.headers.get("X-Goog-Authenticated-User-Email", "")
    if ":" in header:
        return header.rsplit(":", 1)[-1]
    return None


@app.before_request
def require_token():
    """ reject the request unless it carries the shared token (when one is configured) """
    if ACCESS_TOKEN is None:
        return
    supplied = flask.request.args.get("token") or flask.request.cookies.get("token") or ""
    if not hmac.compare_digest(supplied, ACCESS_TOKEN):
        flask.abort(403, "Missing or wrong access token. Ask your instructor for the full link.")


@app.after_request
def remember_token(response):
    """ persist a token that arrived in the url into a cookie """
    if ACCESS_TOKEN is not None and response.status_code < 400:
        supplied = flask.request.args.get("token")
        if supplied and supplied != flask.request.cookies.get("token"):
            response.set_cookie(
                "token",
                supplied,
                max_age=60 * 60 * 24 * 90,
                secure=flask.request.is_secure,
                httponly=True,
            )
    return response


def get_machines():
    """ list all instances in all zones """
    # pylint: disable=no-member
    request = compute.instances().aggregatedList(project=project_id)
    all_instances = []
    while request is not None:
        response = request.execute()
        for _zone, instances_data in response["items"].items():
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
    """ url to see all machines """
    machines = get_machines()
    return flask.render_template("machines.html", machines=machines, user=get_signed_in_user())


@app.route("/process", methods=["POST"])
def process():
    """ toggle the state of a single machine """
    # pylint: disable=no-member
    name = flask.request.form["name"]
    zone = flask.request.form["zone"]
    # decide what to do from the live status, not from whatever the browser
    # showed when the page was last rendered
    instance = compute.instances().get(project=project_id, zone=zone, instance=name).execute()
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
            f"Machine {markupsafe.escape(name)} is currently {markupsafe.escape(status)}, "
            "which means it is already in the middle of a state change."
            "<br/>Go back, refresh and try again in a minute.",
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
        f"Google Cloud API call failed: {markupsafe.escape(error.reason)}"
        "<br/>Go back, refresh and try again.",
        502,
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

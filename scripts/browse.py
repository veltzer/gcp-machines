#!/usr/bin/env python
"""
Open the deployed application in the local web browser.

Looks up the URL of the Cloud Run service and hands it to the default
browser. Handy for checking the app after a deploy or for grabbing the
link to send to students.
"""

import sys
import webbrowser

import google.auth
from googleapiclient import discovery
from googleapiclient.errors import HttpError

# Where the app is deployed. Keep in sync with scripts/deploy.sh.
SERVICE = "machines"
REGION = "us-central1"


def get_url(project_id, credentials):
    """
    Returns the URL the Cloud Run service serves traffic on.
    """
    # pylint: disable=no-member
    run = discovery.build("run", "v2", credentials=credentials)
    service = run.projects().locations().services().get(
        name=f"projects/{project_id}/locations/{REGION}/services/{SERVICE}"
    ).execute()
    return service["uri"]


def main():
    """Main entry point."""
    credentials, project_id = google.auth.default()
    try:
        url = get_url(project_id, credentials)
    except HttpError as error:
        if error.resp.status == 404:
            sys.exit(
                f"Service '{SERVICE}' does not exist in {REGION} yet; "
                "deploy it first with scripts/deploy.sh."
            )
        raise
    print(url)
    if not webbrowser.open(url):
        sys.exit("Could not launch a browser; open the url above yourself.")


if __name__ == "__main__":
    main()

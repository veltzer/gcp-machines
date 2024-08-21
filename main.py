#!/usr/bin/env python

"""
main application
"""

from flask import Flask
from googleapiclient import discovery
from oauth2client.client import GoogleCredentials


app = Flask(__name__)


PROJECT_ID="veltzer-machines-id"
ZONE="us-central1-c"
credentials = GoogleCredentials.get_application_default()
compute = discovery.build("compute", "v1", credentials=credentials)

@app.route("/", methods=["GET"])
def root():
    """ root endpoint """
    html="<html><body>"
    # pylint: disable=no-member
    result = compute.instances().list(project=PROJECT_ID, zone=ZONE).execute()
    instances = result["items"]
    for instance in instances:
        html+=f"Name: {instance['name']}, Status: {instance['status']}"
    html+="</body></html>"
    return html


if __name__ == "__main__":
    app.run(host='127.0.0.1', port=8080, debug=True)

#!/usr/bin/env python

"""
main application
"""

import flask
from googleapiclient import discovery
from oauth2client.client import GoogleCredentials


app = flask.Flask(__name__)


PROJECT_ID="veltzer-machines-id"
credentials = GoogleCredentials.get_application_default()
compute = discovery.build("compute", "v1", credentials=credentials)


def get_machine_data():
    """ get all machine data """
    return []


@app.route("/", methods=["GET"])
def machines():
    """ url to see all machines """
    machines_data = get_machine_data()
    return flask.render_template("machines.html", machines=machines_data)


@app.route('/process/<int:number>')
def process(number):
    """ click on a machine """
    return f"You clicked button number {number}"


if __name__ == "__main__":
    app.run(host='127.0.0.1', port=8080, debug=True)

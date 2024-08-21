#!/usr/bin/env python

"""
main application
"""

from flask import Flask


app = Flask(__name__)


PROJECT_ID="veltzer-machines-id"
ZONE="us-central1-c"

@app.route("/", methods=["GET"])
def root():
    """ root endpoint """
    return "<html>hello</html>"


if __name__ == "__main__":
    app.run(host='127.0.0.1', port=8080, debug=True)

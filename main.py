#!/usr/bin/env python

"""
main application
"""

import flask
from googleapiclient import discovery
from oauth2client.client import GoogleCredentials


app = flask.Flask(
        __name__,
        template_folder="static/templates",
)


PROJECT_ID="veltzer-machines-id"
credentials = GoogleCredentials.get_application_default()
compute = discovery.build("compute", "v1", credentials=credentials)


def get_machines():
    """ list all instances in all zones """
    # pylint: disable=no-member
    request = compute.instances().aggregatedList(project=PROJECT_ID)
    all_instances = []
    while request is not None:
        response = request.execute()
        for _zone, instances_data in response["items"].items():
            instances = instances_data.get("instances", [])
            all_instances.extend(instances)
        request = compute.instances().aggregatedList_next(
                previous_request=request,
                previous_response=response
        )
    # Now you have all instances in the `all_instances` list
    data = []
    i = 0
    for instance in all_instances:
        owner= instance.get("labels")["owner"]
        ip=instance["networkInterfaces"][0]["accessConfigs"][0].get("natIP", "N/A")
        zone = instance["zone"].split("/")[-1]
        data.append({
            "name": instance["name"],
            "status": instance["status"],
            "owner": owner,
            "ip": ip,
            "zone": zone,
            "number": i,
        })
        i = i + 1
    return data


@app.route("/", methods=["GET"])
def root():
    """ url to see all machines """
    machines = get_machines()
    return flask.render_template("machines.html", machines=machines)


@app.route("/process")
def process():
    """ click on a machine """
    machine = flask.request.args.to_dict()
    # pylint: disable=no-member
    zone = machine["zone"]
    name = machine["name"]
    status = machine["status"]
    if status == "SUSPENDED":
        compute.instances().resume(project=PROJECT_ID, zone=zone, instance=name).execute()
    else:
        compute.instances().suspend(project=PROJECT_ID, zone=zone, instance=name).execute()
    return "Machine state changed. Usually it takes 1-2 minutes to really happen. <br/>If you started a machine go back and refresh until you get IP to connect to. Use the same key as before."


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)

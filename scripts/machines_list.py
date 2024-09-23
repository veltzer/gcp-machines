#!/usr/bin/env python

"""
List all machines in all zones
"""

from googleapiclient import discovery
from oauth2client.client import GoogleCredentials
import google.auth


def get_machine_data(project_id, compute):
    """ list all instances in all zones """
    # pylint: disable=no-member
    request = compute.instances().aggregatedList(project=project_id)
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
    for instance in all_instances:
        owner= instance.get("labels")["owner"]
        ip=instance['networkInterfaces'][0]["accessConfigs"][0].get("natIP", "N/A")
        zone = instance['zone'].split('/')[-1]
        data.append({
            "name": instance["name"],
            "status": instance["status"],
            "owner": owner,
            "ip": ip,
            "zone": zone,
        })
    return data

def main():
    """ main entry point """
    _, project_id = google.auth.default()
    credentials = GoogleCredentials.get_application_default()
    compute = discovery.build("compute", "v1", credentials=credentials)
    for d in get_machine_data(project_id, compute):
        print(d)


if __name__ == "__main__":
    main()

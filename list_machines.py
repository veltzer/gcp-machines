#!/usr/bin/env python

"""
List all machines in all zones
"""

from googleapiclient import discovery
from oauth2client.client import GoogleCredentials

# Constants
PROJECT_ID="veltzer-machines-id"
ZONE="us-central1-c"

# Initialize the Compute Engine API client
credentials = GoogleCredentials.get_application_default()
compute = discovery.build("compute", "v1", credentials=credentials)


def get_machine_data():
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

def suspend_instance(instance_name):
    """ suspend one instance """
    # pylint: disable=no-member
    compute.instances().suspend(project=PROJECT_ID, zone=ZONE, instance=instance_name).execute()

def resume_instance(instance_name):
    """ resume one instance """
    # pylint: disable=no-member
    compute.instances().resume(project=PROJECT_ID, zone=ZONE, instance=instance_name).execute()

# Example usage
for d in get_machine_data():
    print(d)

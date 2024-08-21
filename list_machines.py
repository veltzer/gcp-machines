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


def list_instances():
    """ list all instances in all zones """
    # Fetch the list of instances
    # result = compute.instances().list(project=PROJECT_ID, zone=ZONE).execute()
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
    print("All instances:")
    for instance in all_instances:
        owner_label = instance.get("labels")["owner"]
        public_ip=instance['networkInterfaces'][0]["accessConfigs"][0].get("natIP", "N/A")
        print(f"Name: {instance['name']}, Status: {instance['status']}, "
              f"Owner: {owner_label}, Public IP: {public_ip}")

def suspend_instance(instance_name):
    """ suspend one instance """
    # pylint: disable=no-member
    compute.instances().suspend(project=PROJECT_ID, zone=ZONE, instance=instance_name).execute()

def resume_instance(instance_name):
    """ resume one instance """
    # pylint: disable=no-member
    compute.instances().resume(project=PROJECT_ID, zone=ZONE, instance=instance_name).execute()

# Example usage
list_instances()
# suspend_instance("your-instance-name")
# resume_instance("your-instance-name")

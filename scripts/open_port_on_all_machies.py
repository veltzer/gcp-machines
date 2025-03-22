#!/usr/bin/env python

"""
Open a specific port on all of my machines in GCP
"""

from googleapiclient import discovery
from oauth2client.client import GoogleCredentials

credentials = GoogleCredentials.get_application_default()
compute = discovery.build("compute", "v1", credentials=credentials)

port = "8080"
name_all = f"allow-{port}-all"
name = f"allow-{port}"

# Get all instances
# pylint: disable=no-member
instances = compute.instances().aggregatedList(project="veltzer-machines-id").execute()["items"]

for zone, instances_data in instances.items():
    if "instances" in instances_data:
        for instance in instances_data["instances"]:
            instance_name = instance["name"]
            # Get existing firewall rules
            firewall_body = {
                "name": name_all,
                "allowed": [
                    {
                        "IPProtocol": "tcp",
                        "ports": [
                            port,
                        ]
                    }
                ],
                "sourceRanges": [
                    "0.0.0.0/0"
                ],
                "targetTags": [
                    name, # Create a tag for easier management
                ]
            }

            # Create the firewall rule
            # pylint: disable=no-member
            compute.firewalls().insert(project="veltzer-machines-id", body=firewall_body).execute()
            print(f"Firewall rule created to allow port {port} on all instances.")
            # Add the tag to the instance
            tags_body = {
                "items": [
                    name,
                ],
                "fingerprint": instance["tags"]["fingerprint"]
            }
            # pylint: disable=no-member
            compute.instances().setTags(project="veltzer-machines-id", zone=zone.split("/")[-1], instance=instance_name, body=tags_body).execute()
            print(f"Tag [allow-{port}] added to instance {instance_name} in zone {zone}")

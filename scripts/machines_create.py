#!/usr/bin/env python

"""
create machines on the google cloud according to the student list
"""

import time
import os.path
import google.auth
from googleapiclient import discovery
from oauth2client.client import GoogleCredentials

# Set up the Compute Engine API client
_, project_id = google.auth.default()
credentials = GoogleCredentials.get_application_default()
compute = discovery.build("compute", "v1", credentials=credentials)

def create_machine(number, zone, owner, wait, ssh_key):
    """ create a single machine """
    config = {
        "name": f"instance-{number}",
        "machineType": f"zones/{zone}/machineTypes/e2-standard-2",
        "networkInterfaces": [{
            "network": "global/networks/default",
            "accessConfigs": [{
                "name": "External NAT",
                "type": "ONE_TO_ONE_NAT",
                "networkTier": "PREMIUM"
            }],
            "stackType": "IPV4_ONLY"
        }],
        "metadata": {
            "items": [{
                "key": "ssh-keys",
                "value": f"ubuntu:{ssh_key}",
            }]
        },
        "scheduling": {
            "onHostMaintenance": "MIGRATE",
            "provisioningModel": "STANDARD"
        },
        "serviceAccounts": [{
            "email": "57950378250-compute@developer.gserviceaccount.com",
            "scopes": [
                "https://www.googleapis.com/auth/devstorage.read_only",
                "https://www.googleapis.com/auth/logging.write",
                "https://www.googleapis.com/auth/monitoring.write",
                "https://www.googleapis.com/auth/service.management.readonly",
                "https://www.googleapis.com/auth/servicecontrol",
                "https://www.googleapis.com/auth/trace.append"
            ]
        }],
        "disks": [{
            "boot": True,
            "autoDelete": True,
            "initializeParams": {
                "sourceImage": "projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20240720",
                "diskType": f"zones/{zone}/diskTypes/pd-balanced",
                "diskSizeGb": 10
            }
        }],
        "shieldedInstanceConfig": {
            "enableSecureBoot": False,
            "enableVtpm": True,
            "enableIntegrityMonitoring": True
        },
        "labels": {
            "owner": owner,
        },
        "reservationAffinity": {
            "consumeReservationType": "ANY_RESERVATION"
        }
    }
    # Create the VM instance
    # pylint: disable=no-member
    operation = compute.instances().insert(
        project=project_id,
        zone=zone,
        body=config
    ).execute()

    # Wait for the operation to complete
    if not wait:
        return
    while True:
        result = compute.zoneOperations().get(
            project=project_id,
            zone=zone,
            operation=operation["name"]
        ).execute()

        if result["status"] == "DONE":
            print("VM instance created.")
            if "error" in result:
                raise ValueError(result["error"])
            return

    time.sleep(1)  # Wait a bit before checking again

def main():
    """ main entry point """
    with open(os.path.expanduser("~/.ssh/id_machines.pub"), encoding="utf8") as f:
        ssh_key = f.read().rstrip("\n")
    with open("student_list.txt", encoding="utf8") as stream:
        for number, line in enumerate(stream):
            line = line.rstrip()
            (number,owner,zone)=line.split(",")
            wait = True
            print("creating", number, zone, owner)
            create_machine(number, zone, owner, wait, ssh_key)

if __name__ == "__main__":
    main()

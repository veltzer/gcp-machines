#!/usr/bin/env python
"""Delete all compute instances in a GCP project across all zones."""

import google.auth
from googleapiclient import discovery

def main():
    credentials, project_id = google.auth.default()
    compute = discovery.build("compute", "v1", credentials=credentials)

    zones = compute.zones().list(project=project_id).execute()
    for zone in zones["items"]:
        zone_name = zone["name"]
        print(f"zone is [{zone_name}]..")
        instances = compute.instances().list(project=project_id, zone=zone["name"]).execute()
        for instance in instances.get("items", []):
            instance_name = instance["name"]
            print(f"Deleting {instance_name} in {zone_name}")
            compute.instances().delete(project=project_id, zone=zone["name"], instance=instance["name"]).execute()

if __name__ == "__main__":
    main()

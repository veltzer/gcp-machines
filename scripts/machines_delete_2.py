#!/usr/bin/env python
"""Delete all compute instances in a GCP project across all zones."""

import google.auth
from googleapiclient import discovery

def main():
    credentials, project_id = google.auth.default()
    compute = discovery.build("compute", "v1", credentials=credentials)
    instances = compute.instances().aggregatedList(project=project_id).execute()
    for zone, zone_data in instances['items'].items():
        if 'instances' in zone_data:
            zone_name = zone.split('/')[-1]  # Extract zone name from "zones/us-central1-a"
            for instance in zone_data['instances']:
                print(f"Deleting {instance['name']} in {zone_name}")
                compute.instances().delete(project=project_id, zone=zone_name, instance=instance['name']).execute()

if __name__ == "__main__":
    main()

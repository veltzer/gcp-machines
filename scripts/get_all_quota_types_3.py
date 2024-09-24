#!/usr/bin/env python

from googleapiclient.discovery import build

def list_gcp_quotas(project_id):
    """
    Lists all quota types in Google Cloud Platform (GCP), including regional quotas.
    """
    service = build("compute", "v1")

    # Get project-level quotas
    project_quotas = service.projects().get(project=project_id).execute()["quotas"]
    print("Project-level Quotas:")
    for quota in project_quotas:
        print(f"  Quota type: {quota['metric']}")

    # Get regional quotas
    regions = service.regions().list(project=project_id).execute()["items"]
    for region in regions:
        region_name = region["name"]
        print(f"\nRegion: {region_name}")
        region_quotas = region["quotas"]
        for quota in region_quotas:
            # print(f"  Quota type: {quota['metric']}")
            print(f"  Quota type: {quota['metric']}, Limit: {quota['limit']}")

if __name__ == "__main__":
    list_gcp_quotas(project_id="veltzer-machines-id")

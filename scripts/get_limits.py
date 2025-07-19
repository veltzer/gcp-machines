#!/usr/bin/env python
"""
This script retrieves the CPU quota limit for specific GCP regions.
It iterates through a predefined list of regions and prints the CPU limit for each.
"""

import google.auth
from google.cloud import cloudquotas_v1

# A list of regions to check for CPU quotas.
REGIONS = ["us-central1", "us-east1"]
# The specific quota to look for.
QUOTA_ID = "CPUS-per-project-region"

def get_cpu_limits_per_region():
    """
    Fetches and prints the CPU quota for each region defined in the REGIONS list.
    """
    try:
        _, project_id = google.auth.default()
    except google.auth.exceptions.DefaultCredentialsError:
        print("GCP authentication failed. Please configure your credentials.")
        return

    if not project_id:
        print("GCP Project ID not found. Please check your configuration.")
        return

    client = cloudquotas_v1.CloudQuotasClient()

    print(f"Checking CPU limits for project: {project_id}")
    for region in REGIONS:
        # The resource name for the quota, including the project and region.
        parent = f"projects/{project_id}/locations/{region}/services/compute.googleapis.com"

        # The full name of the quota info resource to retrieve.
        quota_name = f"{parent}/quotaInfos/{QUOTA_ID}"

        request = cloudquotas_v1.GetQuotaInfoRequest(name=quota_name)
        quota_info = client.get_quota_info(request=request)

        limit = quota_info.quota_increase_eligibility.ineligibility_reason
        if quota_info.is_precise:
            limit = int(quota_info.details.value)

        print(f"  - Region: {region}, CPU Limit: {limit}")


if __name__ == "__main__":
    get_cpu_limits_per_region()

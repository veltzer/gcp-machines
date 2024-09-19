#!/usr/bin/env python

from google.cloud import cloudquotas_v1
import google.auth

def get_cpu_quota_for_region(region):
    """
    Retrieves the CPU quota for a specific region in the current GCP project.

    Args:
        region: The name of the GCP region (e.g., 'us-central1').

    Returns:
        The CPU quota limit for the specified region, or None if not found.
    """

    _, project_id = google.auth.default()

    if not project_id:
        raise ValueError("Project ID not found. Check your GCP credentials.")

    client = cloudquotas_v1.CloudQuotasClient()
    parent = f"projects/{project_id}/locations/{region}"

    quotas = client.list_quota_infos(parent=parent)

    for quota in quotas:
        if quota.name.endswith("compute.googleapis.com/cpus"):
            return quota.limit.value

    return None

if __name__ == "__main__":
    region = "us-central1"  # Replace with the desired region

    cpu_quota = get_cpu_quota_for_region(region)

    if cpu_quota:
        print(f"CPU quota for region {region}: {cpu_quota}")
    else:
        print(f"CPU quota not found for region {region}")

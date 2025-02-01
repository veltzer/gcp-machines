#!/usr/bin/env python

"""
List all quota id names on GCP
"""

from google.cloud import cloudquotas_v1
import google.auth

def list_all_quota_ids():
    """
    Lists and prints the IDs of all available quotas in the current GCP project.
    """

    _, project_id = google.auth.default()

    if not project_id:
        raise ValueError("Project ID not found. Check your GCP credentials.")

    client = cloudquotas_v1.CloudQuotasClient()
    # service_name = "compute.googleapis.com"
    parent = f"projects/{project_id}/locations/-"
    quotas = client.list_quota_infos(parent=parent)
    for quota in quotas:
        print(quota.name)  # Print the full quota name (includes ID)

if __name__ == "__main__":
    list_all_quota_ids()

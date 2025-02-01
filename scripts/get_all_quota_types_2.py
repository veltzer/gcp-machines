#!/usr/bin/env python

from googleapiclient.discovery import build

def list_gcp_quotas():
    """
    Lists all quota types in Google Cloud Platform (GCP).
    """
    service = build("compute", "v1")
    # pylint: disable=no-member
    quota_types = service.projects().get(project="veltzer-machines-id").execute()["quotas"]
    for quota in quota_types:
        metric = quota["metric"]
        print(f"Quota type: {metric}")

if __name__ == "__main__":
    list_gcp_quotas()

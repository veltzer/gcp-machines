#!/usr/bin/env python

"""
List all regions in GCP
"""

from googleapiclient import discovery
import google.auth

def list_all_regions(project_id: str) -> None:
    """
    Lists and prints all available Google Cloud regions.
    """
    credentials, _ = google.auth.default()
    compute = discovery.build("compute", "v1", credentials=credentials)
    regions = compute.regions().list(project=project_id).execute()
    for region in regions["items"]:
        print(region["name"])

def main():
    """ main entry point """
    _, project_id = google.auth.default()
    list_all_regions(project_id)


if __name__ == "__main__":
    main()

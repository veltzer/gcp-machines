#!/usr/bin/env python

from google.cloud import compute_v1
import google.auth

def list_all_regions(project_id: str) -> None:
    """
    Lists and prints all available Google Cloud regions.
    """
    compute_client = compute_v1.RegionsClient()
    regions = compute_client.list(project=project_id)
    for region in regions:
        print(region.name)

def main():
    """ main entry point """
    _, project_id = google.auth.default()
    list_all_regions(project_id)


if __name__ == "__main__":
    main()

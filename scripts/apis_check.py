#!/usr/bin/env python
"""
Checks the status of essential Google Cloud services for the project.
This script verifies that "compute.googleapis.com", "datastore.googleapis.com",
and "gmail.googleapis.com" are enabled.
"""

import google.auth
from googleapiclient import discovery

def check_service_status(project_id, service_name, service_usage):
    """
    Checks if a specific service is enabled for the project.
    """
    # pylint: disable=no-member
    request = service_usage.services().get(
        name=f"projects/{project_id}/services/{service_name}"
    )
    response = request.execute()
    state = response.get("state")
    print(f"Service [{service_name}] is [{state}]")


def main():
    """
    Main function to initialize the API client and check services.
    """
    credentials, project_id = google.auth.default()
    service_usage = discovery.build("serviceusage", "v1", credentials=credentials)

    services_to_check = [
        "compute.googleapis.com",
        "datastore.googleapis.com",
        "gmail.googleapis.com",
        "cloudquotas.googleapis.com",
    ]

    print(f"Checking services for project: {project_id}")
    for service in services_to_check:
        check_service_status(project_id, service, service_usage)

if __name__ == "__main__":
    main()

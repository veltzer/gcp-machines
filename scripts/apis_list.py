#!/usr/bin/env python
"""
Lists all enabled Google Cloud services for the current project.
"""

import google.auth
from googleapiclient import discovery

def list_enabled_apis():
    """
    Fetches and prints all enabled APIs for the current GCP project.
    """
    credentials, project_id = google.auth.default()
    service_usage = discovery.build("serviceusage", "v1", credentials=credentials)
    parent = f"projects/{project_id}"
    print(f"Listing enabled APIs for project: {project_id}")
    # pylint: disable=no-member
    request = service_usage.services().list(parent=parent, filter="state:ENABLED")
    while request is not None:
        response = request.execute()
        for service in response.get("services", []):
            service_name = service.get("config", {}).get("name", "N/A")
            print(f"{service_name}")
            request = service_usage.services().list_next(
                previous_request=request, previous_response=response
            )

if __name__ == "__main__":
    list_enabled_apis()

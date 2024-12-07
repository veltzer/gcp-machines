#!/usr/bin/env python

# pylint: disable=no-name-in-module
from google.cloud import compute_v1
import google.auth

def list_compute_engine_quotas(project_id):
    # Initialize the Compute Engine client
    client = compute_v1.ProjectsClient()

    # Fetch the project details including quotas
    project = client.get(project=project_id)

    # Print all the quotas
    print(f"Quotas for project {project_id}:")
    for quota in project.quotas:
        print(f"Metric: {quota.metric}")
        print(f"  Limit: {quota.limit}")
        print(f"  Usage: {quota.usage}")
        print()

def main():
    _, project_id = google.auth.default()
    list_compute_engine_quotas(project_id)


if __name__ == "__main__":
    main()

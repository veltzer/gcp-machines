#!/usr/bin/env python

from google.cloud import resourcemanager_v3
from google.api_core.exceptions import GoogleAPICallError

def list_regions():
    """
    Lists all available GCP regions using the Resource Manager API.
    """
    client = resourcemanager_v3.ProjectsClient()

    try:
        # Get the list of all available locations
        request = resourcemanager_v3.ListAvailableLocationsRequest()
        response = client.list_available_locations(request=request)

        # Filter and print the regions
        regions = [loc for loc in response.locations if not loc.location_id.startswith('us-')]

        for region in regions:
            print(f"Region: {region.location_id}")
            print(f"  Display Name: {region.display_name}")
            print(f"  Description: {region.metadata.get('description', 'N/A')}")
            print("---")

    except GoogleAPICallError as e:
        print(f"An error occurred: {e}")

# Usage
list_regions()

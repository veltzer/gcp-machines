#!/usr/bin/env python

from google.cloud import datastore
import logging

def list_datastore_entities(project_id):
    """Lists all entities and their properties in a Datastore kind.

    Args:
        project_id: The ID of your Google Cloud project.
    """

    datastore_client = datastore.Client(project=project_id)

    query = datastore_client.query()  # Query all kinds
    try:
        for entity in query.fetch():
            print(f"Entity Key: {entity.key}")
            for property_name, property_value in entity.items():
                print(f"  {property_name}: {property_value}")
    except Exception as e:
        logging.error(f"Error listing Datastore entities: {e}")

if __name__ == "__main__":
    project_id = "veltzer-machines-id"
    list_datastore_entities(project_id)

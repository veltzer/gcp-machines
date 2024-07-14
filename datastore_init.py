#!/usr/bin/env python

from google.cloud import datastore

# Datastore Client
datastore_client = datastore.Client()

# Machine Status Model (Entity Kind)
MACHINE_STATUS_KIND = "MachineStatus"

def initialize_datastore():
    for username in ["user1", "user2"]:  # Add more users as needed
        key = datastore_client.key(MACHINE_STATUS_KIND, username)
        entity = datastore.Entity(key)
        entity["status"] = "DOWN"
        entity["uptime_hours"] = 1  # Default uptime
        datastore_client.put(entity)

if __name__ == "__main__":
    initialize_datastore()

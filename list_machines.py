#!/usr/bin/env python

from googleapiclient import discovery
from oauth2client.client import GoogleCredentials

# Constants
PROJECT_ID="veltzer-machines-id"
ZONE="us-central1-c"

# Initialize the Compute Engine API client
credentials = GoogleCredentials.get_application_default()
compute = discovery.build('compute', 'v1', credentials=credentials)

def list_instances():
    # Fetch the list of instances
    result = compute.instances().list(project=PROJECT_ID, zone=ZONE).execute()
    instances = result['items'] if 'items' in result else None
    
    for instance in instances:
        print(f"Name: {instance['name']}, Status: {instance['status']}") 
        # Add logic to print labels and external IPs if needed

def suspend_instance(instance_name):
    compute.instances().suspend(project=PROJECT_ID, zone=ZONE, instance=instance_name).execute()

def resume_instance(instance_name):
    compute.instances().resume(project=PROJECT_ID, zone=ZONE, instance=instance_name).execute()

# Example usage
list_instances()
# suspend_instance('your-instance-name')
# resume_instance('your-instance-name')

#!/usr/bin/env python
"""
Manage compute instances in a GCP project.
This script provides a command-line interface to create, list, stop, and delete virtual machines.
"""

import sys
import json
import argparse
import os
import time
import google.auth
from googleapiclient import discovery
from oauth2client.client import GoogleCredentials

def get_compute_client():
    """Initializes and returns a Compute Engine API client."""
    credentials = GoogleCredentials.get_application_default()
    return discovery.build("compute", "v1", credentials=credentials)

def list_machines(project_id, compute):
    """
    Lists all instances in the project, returning a list of machine data.
    """
    all_instances = []
    request = compute.instances().aggregatedList(project=project_id)
    while request is not None:
        response = request.execute()
        for _zone, instances_data in response["items"].items():
            instances = instances_data.get("instances", [])
            all_instances.extend(instances)
        request = compute.instances().aggregatedList_next(
            previous_request=request, previous_response=response
        )

    data = []
    for instance in all_instances:
        owner = instance.get("labels", {}).get("owner", "N/A")
        ip = instance["networkInterfaces"][0]["accessConfigs"][0].get("natIP", "N/A")
        zone = instance["zone"].split("/")[-1]
        data.append({
            "name": instance["name"],
            "status": instance["status"],
            "owner": owner,
            "ip": ip,
            "zone": zone,
        })
    return data

def create_machine(project_id, compute, number, zone, owner, wait, ssh_key):
    """
    Creates a single virtual machine instance.
    """
    print(f"Creating instance-{number} in {zone} for {owner}...")
    config = {
        "name": f"instance-{number}",
        "machineType": f"zones/{zone}/machineTypes/e2-standard-2",
        "networkInterfaces": [{
            "network": "global/networks/default",
            "accessConfigs": [{"name": "External NAT", "type": "ONE_TO_ONE_NAT"}],
        }],
        "metadata": {"items": [{"key": "ssh-keys", "value": f"ubuntu:{ssh_key}"}]},
        "disks": [{
            "boot": True,
            "autoDelete": True,
            "initializeParams": {
                "sourceImage": "projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20240720",
                "diskSizeGb": 10,
            },
        }],
        "labels": {"owner": owner},
    }
    operation = compute.instances().insert(project=project_id, zone=zone, body=config).execute()

    if wait:
        while True:
            result = compute.zoneOperations().get(
                project=project_id, zone=zone, operation=operation["name"]
            ).execute()
            if result["status"] == "DONE":
                if "error" in result:
                    raise ValueError(result["error"])
                print(f"Instance instance-{number} created successfully.")
                break
            time.sleep(1)

def stop_all_machines(project_id, compute):
    """
    Stops all running compute instances in the project.
    """
    instances = compute.instances().aggregatedList(project=project_id).execute()
    for zone, zone_data in instances.get("items", {}).items():
        zone_name = zone.split("/")[-1]
        for instance in zone_data.get("instances", []):
            if instance["status"] == "RUNNING":
                print(f"Stopping {instance['name']} in {zone_name}...")
                compute.instances().stop(
                    project=project_id, zone=zone_name, instance=instance["name"]
                ).execute()
            else:
                print(f"Skipping {instance['name']} in {zone_name} (status: {instance['status']})")

def delete_all_machines(project_id, compute):
    """
    Deletes all compute instances in the project.
    """
    if input("This will delete ALL instances. Are you sure? (y/n): ").lower() != 'y':
        print("Aborted.")
        return
    instances = compute.instances().aggregatedList(project=project_id).execute()
    for zone, zone_data in instances.get("items", {}).items():
        zone_name = zone.split("/")[-1]
        for instance in zone_data.get("instances", []):
            print(f"Deleting {instance['name']} in {zone_name}...")
            compute.instances().delete(
                project=project_id, zone=zone_name, instance=instance["name"]
            ).execute()

def main():
    """Main entry point and command-line parser."""
    parser = argparse.ArgumentParser(description="Manage GCP compute instances.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # List command
    list_parser = subparsers.add_parser("list", help="List all VM instances.")
    list_parser.set_defaults(func=lambda args, proj, comp: json.dump(list_machines(proj, comp), fp=sys.stdout))

    # Create command
    create_parser = subparsers.add_parser("create", help="Create VM instances from a file.")
    create_parser.add_argument(
        "--student-list", default="student_list.txt", help="Path to the student list file."
    )
    create_parser.add_argument(
        "--ssh-key-file", default="~/.ssh/id_machines.pub", help="Path to the public SSH key."
    )
    create_parser.add_argument(
        "--no-wait", action="store_true", help="Don't wait for instance creation to complete."
    )
    def create_command(args, project_id, compute):
        ssh_key_path = os.path.expanduser(args.ssh_key_file)
        with open(ssh_key_path, "r", encoding="utf-8") as f:
            ssh_key = f.read().strip()
        with open(args.student_list, "r", encoding="utf-8") as f:
            for line in f:
                number, owner, zone = line.strip().split(",")
                create_machine(project_id, compute, number, zone, owner, not args.no_wait, ssh_key)
    create_parser.set_defaults(func=create_command)

    # Stop-all command
    stop_parser = subparsers.add_parser("stop-all", help="Stop all running VM instances.")
    stop_parser.set_defaults(func=lambda args, proj, comp: stop_all_machines(proj, comp))

    # Delete-all command
    delete_parser = subparsers.add_parser("delete-all", help="Delete all VM instances.")
    delete_parser.set_defaults(func=lambda args, proj, comp: delete_all_machines(proj, comp))

    args = parser.parse_args()
    _, project_id = google.auth.default()
    compute = get_compute_client()
    args.func(args, project_id, compute)

if __name__ == "__main__":
    main()

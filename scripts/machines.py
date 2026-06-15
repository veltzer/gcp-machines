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

# Always read the student list from data.gi/student_list.txt at the repo root.
STUDENT_LIST_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data.gi",
    "student_list.txt",
)

# Always read the SSH public key from ~/.ssh/id_machines.pub.
SSH_KEY_FILE = os.path.expanduser("~/.ssh/id_machines.pub")

def get_compute_client():
    """Initializes and returns a Compute Engine API client."""
    credentials, _ = google.auth.default()
    return discovery.build("compute", "v1", credentials=credentials)

def get_all_instances(project_id, compute):
    """
    Returns the raw list of all instances in the project.
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
    return all_instances

def list_machines_full(project_id, compute):
    """
    Lists all instances in the project, returning the full JSON info for each.
    """
    return get_all_instances(project_id, compute)

def list_machines(project_id, compute):
    """
    Lists all instances in the project, returning only the student (owner)
    and public IP for each.
    """
    data = []
    for instance in get_all_instances(project_id, compute):
        owner = instance.get("labels", {}).get("owner", "N/A")
        ip = instance["networkInterfaces"][0]["accessConfigs"][0].get("natIP", "N/A")
        data.append({
            "owner": owner,
            "ip": ip,
        })
    return data

def print_machines_table(data):
    """
    Prints a list of {"owner", "ip"} dicts as an aligned text table.
    """
    headers = ("OWNER", "IP")
    rows = [(row["owner"], row["ip"]) for row in data]
    owner_width = max([len(headers[0])] + [len(r[0]) for r in rows])
    ip_width = max([len(headers[1])] + [len(r[1]) for r in rows])
    print(f"{headers[0]:<{owner_width}}  {headers[1]:<{ip_width}}")
    for owner, ip in rows:
        print(f"{owner:<{owner_width}}  {ip:<{ip_width}}")

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
    list_parser = subparsers.add_parser("list", help="List students and public IPs of all VM instances.")
    list_parser.set_defaults(func=lambda args, proj, comp: print_machines_table(list_machines(proj, comp)))

    # List-full command
    list_full_parser = subparsers.add_parser("list-json", help="List full JSON info about all VM instances.")
    list_full_parser.set_defaults(func=lambda args, proj, comp: json.dump(list_machines_full(proj, comp), fp=sys.stdout))

    # Create command
    create_parser = subparsers.add_parser("create", help="Create VM instances from a file.")
    create_parser.add_argument(
        "--no-wait", action="store_true", help="Don't wait for instance creation to complete."
    )
    def create_command(args, project_id, compute):
        with open(SSH_KEY_FILE, "r", encoding="utf-8") as f:
            ssh_key = f.read().strip()
        with open(STUDENT_LIST_FILE, "r", encoding="utf-8") as f:
            for line in f:
                number, owner, zone = line.strip().split(",")
                create_machine(project_id, compute, number, zone, owner, not args.no_wait, ssh_key)
    create_parser.set_defaults(func=create_command)

    # Stop-all command
    stop_parser = subparsers.add_parser("stop", help="Stop all running VM instances.")
    stop_parser.set_defaults(func=lambda args, proj, comp: stop_all_machines(proj, comp))

    # Delete-all command
    delete_parser = subparsers.add_parser("delete", help="Delete all VM instances.")
    delete_parser.set_defaults(func=lambda args, proj, comp: delete_all_machines(proj, comp))

    args = parser.parse_args()
    _, project_id = google.auth.default()
    compute = get_compute_client()
    args.func(args, project_id, compute)

if __name__ == "__main__":
    main()

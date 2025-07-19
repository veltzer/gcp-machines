#!/usr/bin/env python
"""
Manage firewall rules for a GCP project.
This script can be used to create firewall rules to open specific ports or all ports for all instances.
"""

import argparse
import google.auth
from googleapiclient import discovery
from googleapiclient.errors import HttpError

def get_compute_client():
    """Initializes and returns a Compute Engine API client."""
    credentials, _ = google.auth.default()
    return discovery.build("compute", "v1", credentials=credentials)

def create_firewall_rule(project_id, compute, name, allowed, target_tag):
    """
    Creates a firewall rule in the project.
    """
    firewall_body = {
        "name": name,
        "allowed": allowed,
        "sourceRanges": ["0.0.0.0/0"],
        "targetTags": [target_tag],
    }
    try:
        print(f"Creating firewall rule '{name}'...")
        compute.firewalls().insert(project=project_id, body=firewall_body).execute()
        print(f"Firewall rule '{name}' created successfully.")
    except HttpError as e:
        if e.resp.status == 409:  # 409 is the status code for "Conflict" (already exists)
            print(f"Firewall rule '{name}' already exists. Skipping.")
        else:
            raise

def add_tag_to_all_instances(project_id, compute, tag):
    """
    Adds a network tag to all instances in the project.
    """
    instances = compute.instances().aggregatedList(project=project_id).execute()
    for zone, zone_data in instances.get("items", {}).items():
        zone_name = zone.split("/")[-1]
        for instance in zone_data.get("instances", []):
            instance_name = instance["name"]
            tags = instance.get("tags", {})
            fingerprint = tags.get("fingerprint", "")
            items = tags.get("items", [])
            if tag not in items:
                items.append(tag)
                tags_body = {"items": items, "fingerprint": fingerprint}
                print(f"Adding tag '{tag}' to instance {instance_name} in {zone_name}...")
                compute.instances().setTags(
                    project=project_id, zone=zone_name, instance=instance_name, body=tags_body
                ).execute()
            else:
                print(f"Tag '{tag}' already present on instance {instance_name}. Skipping.")

def main():
    """Main entry point and command-line parser."""
    parser = argparse.ArgumentParser(description="Manage GCP firewall rules.")
    parser.add_argument("--port", type=int, help="A specific TCP port to open.")
    parser.add_argument("--all", action="store_true", help="Open all TCP, UDP, and ICMP ports.")

    args = parser.parse_args()

    if not args.port and not args.all:
        parser.error("You must specify either --port or --all.")
    if args.port and args.all:
        parser.error("You cannot specify both --port and --all.")

    _, project_id = google.auth.default()
    compute = get_compute_client()

    if args.all:
        rule_name = "allow-all-ports"
        tag = "allow-all"
        allowed = [
            {"IPProtocol": "tcp", "ports": ["1-65535"]},
            {"IPProtocol": "udp", "ports": ["1-65535"]},
            {"IPProtocol": "icmp"},
        ]
        create_firewall_rule(project_id, compute, rule_name, allowed, tag)
        add_tag_to_all_instances(project_id, compute, tag)
    elif args.port:
        rule_name = f"allow-tcp-{args.port}"
        tag = rule_name
        allowed = [{"IPProtocol": "tcp", "ports": [str(args.port)]}]
        create_firewall_rule(project_id, compute, rule_name, allowed, tag)
        add_tag_to_all_instances(project_id, compute, tag)

if __name__ == "__main__":
    main()

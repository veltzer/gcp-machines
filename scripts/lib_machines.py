#!/usr/bin/env python
"""
Shared helpers for the scripts that reach a student's machine over ssh
(connect.py, tunnel.py).

The point of these scripts is that no IP is ever written down: a machine is
identified by the "owner" label it was created with, and its external IP is
read live from the Compute API at the moment of connecting. Suspending and
resuming a machine changes its IP, so anything cached would be stale.
"""

import os
import sys

# The private half of the key whose public half machines.py injects into
# every instance at creation time (see SSH_KEY_FILE there).
SSH_KEY_FILE = os.path.expanduser("~/.ssh/id_machines")

# The user machines.py creates the ssh-keys metadata for.
DEFAULT_USER = "ubuntu"

def add_target_arguments(parser):
    """
    Adds the arguments shared by every script that ssh-es to a machine: which
    owner to reach, as which user, and with which key.
    """
    parser.add_argument("owner", help="Owner name of the machine, as in data.gi/students.txt.")
    parser.add_argument(
        "--user",
        default=DEFAULT_USER,
        help=f"User to log in as (default: {DEFAULT_USER}).",
    )
    parser.add_argument(
        "--key",
        default=None,
        help=f"Private key to use (default: {SSH_KEY_FILE}).",
    )

def ssh_key_path(key):
    """
    Returns the private key path to use, defaulting to SSH_KEY_FILE, and
    exits with an actionable message if it is not there.
    """
    path = os.path.expanduser(key) if key else SSH_KEY_FILE
    if not os.path.exists(path):
        sys.exit(
            f"No ssh private key at {path}.\n"
            "This is the private half of the key machines.py injects into every\n"
            f"instance ({SSH_KEY_FILE}.pub). Put it there, or pass --key."
        )
    return path

def get_all_instances(project_id, compute):
    """
    Returns the raw list of all instances in the project.
    """
    all_instances = []
    request = compute.instances().aggregatedList(project=project_id)
    while request is not None:
        response = request.execute()
        for _zone, instances_data in response["items"].items():
            all_instances.extend(instances_data.get("instances", []))
        request = compute.instances().aggregatedList_next(
            previous_request=request, previous_response=response
        )
    return all_instances

def instance_ip(instance):
    """
    Returns the external IP of an instance, or None when it has none (a
    stopped or suspended machine has no external IP).
    """
    interfaces = instance.get("networkInterfaces", [])
    if not interfaces:
        return None
    access_configs = interfaces[0].get("accessConfigs", [])
    if not access_configs:
        return None
    return access_configs[0].get("natIP")

def find_by_owner(project_id, compute, owner):
    """
    Returns the instances labelled with the given owner.
    """
    return [
        instance
        for instance in get_all_instances(project_id, compute)
        if instance.get("labels", {}).get("owner") == owner
    ]

def owner_ip(project_id, compute, owner):
    """
    Returns the current external IP of the machine belonging to an owner.

    Exits with an actionable message when there is no such machine, when the
    machine is not running (and so has no IP to connect to), or when the name
    is ambiguous, rather than handing ssh something that cannot work.
    """
    instances = find_by_owner(project_id, compute, owner)
    if not instances:
        known = sorted({
            label
            for instance in get_all_instances(project_id, compute)
            if (label := instance.get("labels", {}).get("owner")) is not None
        })
        sys.exit(
            f"No machine labelled owner='{owner}'.\n"
            + (f"Known owners: {', '.join(known)}" if known
               else "No machine in the project carries an owner label.")
        )
    if len(instances) > 1:
        names = ", ".join(sorted(instance["name"] for instance in instances))
        sys.exit(
            f"{len(instances)} machines are labelled owner='{owner}': {names}.\n"
            "Owner labels are meant to be unique; fix the labels and retry."
        )
    instance = instances[0]
    ip = instance_ip(instance)
    if ip is None:
        status = instance["status"]
        # a stopped machine reports TERMINATED, which reads as if it is gone
        friendly = "STOPPED" if status == "TERMINATED" else status
        sys.exit(
            f"Machine {instance['name']} (owner '{owner}') is {friendly} and has "
            "no external IP.\n"
            "Start it from the web app, or with: python scripts/machines.py continue"
        )
    return ip

def strip_separator(extra_args):
    """
    Drops the leading "--" argparse.REMAINDER keeps when the user separates
    pass-through arguments with it, so it is not forwarded to ssh.
    """
    if extra_args and extra_args[0] == "--":
        return extra_args[1:]
    return extra_args

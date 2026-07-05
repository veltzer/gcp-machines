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

# The only zones in which we are allowed to create machines.
ALLOWED_ZONES = ("us-central1-a", "us-east1-c")

# The machine type used for every instance, and how many vCPUs it consumes.
# Used both when creating machines and when computing per-zone limits.
MACHINE_TYPE = "e2-standard-2"
MACHINE_VCPUS = 2

def require_default_account(credentials):
    """
    Refuses to run unless we are authenticating as the default (personal)
    account rather than a service account.

    This project is administered as the project owner (the default account);
    the service account is only for the deployed app. Service-account
    credentials carry a service_account_email; user (default) credentials do
    not.
    """
    sa_email = getattr(credentials, "service_account_email", None)
    if sa_email is not None:
        sys.exit(
            f"Refusing to run as service account '{sa_email}'.\n"
            "This script must run as your default (personal) account.\n"
            "Set gcp_identity=default in .gcp.conf (or unset "
            "GOOGLE_APPLICATION_CREDENTIALS / open a fresh shell), then re-run."
        )

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

# The three ways of waiting for a batch of asynchronous zone operations.
#   "none"  - fire every operation and return immediately, waiting for none.
#   "each"  - fire each operation and wait for it to finish before firing the
#             next one (serial; slowest, but easiest to reason about).
#   "all"   - fire every operation up front, then wait for all of them to
#             finish (parallel; the default and usually the fastest).
WAIT_NONE = "none"
WAIT_EACH = "each"
WAIT_ALL = "all"

def wait_for_operation(project_id, compute, zone, op_name, action="finish"):
    """
    Blocks until a single zone operation finishes, raising on error. action
    describes what we're waiting for (e.g. "come up", "be deleted") and is used
    only for the progress message.
    """
    print(f"Waiting for 1 machine to {action}...")
    while True:
        result = compute.zoneOperations().get(
            project=project_id, zone=zone, operation=op_name
        ).execute()
        if result["status"] == "DONE":
            if "error" in result:
                raise ValueError(result["error"])
            return
        time.sleep(1)

def wait_for_operations(project_id, compute, operations, action="finish"):
    """
    Blocks until every (zone, op_name) operation in the list finishes. action
    describes what we're waiting for (e.g. "come up", "be deleted") and is used
    only for the progress message.
    """
    if not operations:
        return
    print(f"Waiting for {len(operations)} machines to {action}...")
    for zone, op_name in operations:
        wait_for_operation_quiet(project_id, compute, zone, op_name)

def wait_for_operation_quiet(project_id, compute, zone, op_name):
    """
    Blocks until a single zone operation finishes, raising on error, without
    printing a message. Used by wait_for_operations, which prints its own
    batch-level message.
    """
    while True:
        result = compute.zoneOperations().get(
            project=project_id, zone=zone, operation=op_name
        ).execute()
        if result["status"] == "DONE":
            if "error" in result:
                raise ValueError(result["error"])
            return
        time.sleep(1)

def create_machine(project_id, compute, number, zone, owner, ssh_key):
    """
    Fires off creation of a single virtual machine instance and returns the
    (zone, operation-name) pair for the insert operation. Does not wait.
    """
    print(f"Creating instance-{number} in {zone} for {owner}...")
    config = {
        "name": f"instance-{number}",
        "machineType": f"zones/{zone}/machineTypes/{MACHINE_TYPE}",
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
    return (zone, operation["name"])

def stop_all_machines(project_id, compute, wait_mode):
    """
    Stops all running compute instances in the project.

    wait_mode (WAIT_NONE / WAIT_EACH / WAIT_ALL) controls how stop operations
    are waited on; see the WAIT_* constants.
    """
    instances = compute.instances().aggregatedList(project=project_id).execute()
    operations = []
    for zone, zone_data in instances.get("items", {}).items():
        zone_name = zone.split("/")[-1]
        for instance in zone_data.get("instances", []):
            if instance["status"] == "RUNNING":
                print(f"Stopping {instance['name']} in {zone_name}...")
                operation = compute.instances().stop(
                    project=project_id, zone=zone_name, instance=instance["name"]
                ).execute()
                if wait_mode == WAIT_EACH:
                    wait_for_operation(project_id, compute, zone_name, operation["name"], "stop")
                else:
                    operations.append((zone_name, operation["name"]))
            else:
                print(f"Skipping {instance['name']} in {zone_name} (status: {instance['status']})")

    if wait_mode == WAIT_ALL:
        wait_for_operations(project_id, compute, operations, "stop")

def delete_all_machines(project_id, compute, wait_mode):
    """
    Deletes all compute instances in the project.

    wait_mode (WAIT_NONE / WAIT_EACH / WAIT_ALL) controls how delete operations
    are waited on; see the WAIT_* constants. Waiting matters here because
    deletes are asynchronous: without it a subsequent `list` may still report
    the instances being deleted.
    """
    if input("This will delete ALL instances. Are you sure? (y/n): ").lower() != 'y':
        print("Aborted.")
        return
    instances = compute.instances().aggregatedList(project=project_id).execute()
    operations = []
    for zone, zone_data in instances.get("items", {}).items():
        zone_name = zone.split("/")[-1]
        for instance in zone_data.get("instances", []):
            print(f"Deleting {instance['name']} in {zone_name}...")
            operation = compute.instances().delete(
                project=project_id, zone=zone_name, instance=instance["name"]
            ).execute()
            if wait_mode == WAIT_EACH:
                wait_for_operation(project_id, compute, zone_name, operation["name"], "be deleted")
            else:
                operations.append((zone_name, operation["name"]))

    if wait_mode == WAIT_ALL:
        wait_for_operations(project_id, compute, operations, "be deleted")

# Cache of per-region CPUS quota limit, keyed by region name. Populated lazily
# by zone_machine_limit so zones sharing a region don't re-fetch it.
_REGION_CPU_LIMIT_CACHE: dict[str, int | None] = {}

def zone_machine_limit(project_id, compute, zone):
    """
    Returns the absolute cap on how many machines of our standard machine
    type can be created in a zone, based on the region's CPU quota, or None
    if the region has no CPUS quota.

    CPU quota in GCP is per-region, so each zone is mapped to its region
    (e.g. "us-central1-a" -> "us-central1"). The cap is the full
    CPUS quota limit // vCPUs-per-machine, regardless of current usage.
    """
    region = zone.rsplit("-", 1)[0]
    if region not in _REGION_CPU_LIMIT_CACHE:
        region_info = compute.regions().get(project=project_id, region=region).execute()
        cpu_quota = next(
            (q for q in region_info.get("quotas", []) if q["metric"] == "CPUS"),
            None,
        )
        _REGION_CPU_LIMIT_CACHE[region] = None if cpu_quota is None else int(cpu_quota["limit"])
    cpu_limit = _REGION_CPU_LIMIT_CACHE[region]
    return None if cpu_limit is None else cpu_limit // MACHINE_VCPUS

def list_regions(project_id, compute):
    """
    Lists and prints the names of all available Google Cloud regions.
    """
    regions = compute.regions().list(project=project_id).execute()
    for region in regions["items"]:
        print(region["name"])

def machine_limits(project_id, compute):
    """
    For every zone in the project, prints the absolute cap on how many
    machines of our standard machine type can be created there.
    """
    # Gather every zone in the project.
    zone_names = []
    request = compute.zones().list(project=project_id)
    while request is not None:
        response = request.execute()
        for zone in response.get("items", []):
            zone_names.append(zone["name"])
        request = compute.zones().list_next(previous_request=request, previous_response=response)

    rows = []
    for zone in sorted(zone_names):
        limit = zone_machine_limit(project_id, compute, zone)
        allowed = "N/A" if limit is None else str(limit)
        rows.append((zone, allowed))

    zone_width = max([len("ZONE")] + [len(z) for z, _ in rows])
    print(f"{'ZONE':<{zone_width}}  MACHINES ({MACHINE_TYPE})")
    for zone, allowed in rows:
        print(f"{zone:<{zone_width}}  {allowed}")

def show_input_sample():
    """
    Prints a fake 10-line sample of the input expected by `create`, so the
    user can see what a valid student list file looks like. Each line is a
    single owner name; zones are assigned automatically at create time.
    """
    owners = [
        "alice", "bob", "carol", "dave", "eve",
        "frank", "grace", "heidi", "ivan", "judy",
    ]
    for owner in owners:
        print(owner)

def create_command(args, project_id, compute):
    """
    Creates one VM per owner in the student list, allocating owners to zones up
    to each zone's machine limit, and waits according to args.wait_mode.
    """
    with open(SSH_KEY_FILE, "r", encoding="utf-8") as f:
        ssh_key = f.read().strip()
    with open(STUDENT_LIST_FILE, "r", encoding="utf-8") as f:
        owners = [line.strip() for line in f if line.strip()]

    # Check owner uniqueness BEFORE creating anything, so we fail fast
    # instead of partway through creating machines.
    seen = set()
    duplicates = set()
    for owner in owners:
        if owner in seen:
            duplicates.add(owner)
        seen.add(owner)
    if duplicates:
        raise ValueError(
            f"Duplicate owner(s) in {STUDENT_LIST_FILE}: "
            f"{', '.join(sorted(duplicates))}"
        )

    # Allocate owners to zones by walking ALLOWED_ZONES in order, filling
    # each zone up to its per-zone machine limit before moving to the next.
    # The whole allocation is computed BEFORE creating anything so we fail
    # fast if there isn't enough total capacity.
    assignments = []  # (owner, zone), in file order
    remaining = list(owners)
    for zone in ALLOWED_ZONES:
        if not remaining:
            break
        limit = zone_machine_limit(project_id, compute, zone)
        if limit is None:
            continue
        take, remaining = remaining[:limit], remaining[limit:]
        assignments.extend((owner, zone) for owner in take)
    if remaining:
        capacity = sum(
            limit
            for zone in ALLOWED_ZONES
            if (limit := zone_machine_limit(project_id, compute, zone)) is not None
        )
        raise ValueError(
            f"Not enough capacity in allowed zones for {len(owners)} machine(s): "
            f"total capacity is {capacity} across {', '.join(ALLOWED_ZONES)}. "
            f"Owner(s) left unassigned: {', '.join(remaining)}"
        )

    # The instance number is derived from the line position in the file.
    # Fire each insert; in WAIT_EACH mode block on it before the next, in
    # WAIT_ALL mode collect the operations and wait for all at the end, and
    # in WAIT_NONE mode never wait.
    operations = []
    for number, (owner, zone) in enumerate(assignments):
        zone_name, op_name = create_machine(
            project_id, compute, number, zone, owner, ssh_key
        )
        if args.wait_mode == WAIT_EACH:
            wait_for_operation(project_id, compute, zone_name, op_name, "come up")
            print(f"Instance instance-{number} created successfully.")
        else:
            operations.append((zone_name, op_name))
    if args.wait_mode == WAIT_ALL:
        wait_for_operations(project_id, compute, operations, "come up")

def add_wait_flag(subparser):
    """
    Adds the mutually-exclusive --wait-all / --wait-each / --no-wait flags
    (default WAIT_ALL) to a subparser, stored in args.wait_mode.
    """
    group = subparser.add_mutually_exclusive_group()
    group.add_argument(
        "--wait-all", dest="wait_mode", action="store_const", const=WAIT_ALL,
        default=WAIT_ALL,
        help="Fire all operations, then wait for all of them (default).",
    )
    group.add_argument(
        "--wait-each", dest="wait_mode", action="store_const", const=WAIT_EACH,
        help="Wait for each operation to finish before starting the next.",
    )
    group.add_argument(
        "--no-wait", dest="wait_mode", action="store_const", const=WAIT_NONE,
        help="Fire all operations and don't wait for any of them.",
    )

def main():
    """Main entry point and command-line parser."""
    parser = argparse.ArgumentParser(description="Manage GCP compute instances.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # List command
    list_parser = subparsers.add_parser("list", help="List students and public IPs of all VM instances.")
    list_parser.set_defaults(func=lambda args, proj, comp: print_machines_table(list_machines(proj, comp)))

    # Machine-limits command
    limits_parser = subparsers.add_parser(
        "machine-limits",
        help="Show how many machines can be created in each allowed zone.",
    )
    limits_parser.set_defaults(func=lambda args, proj, comp: machine_limits(proj, comp))

    # List-regions command
    regions_parser = subparsers.add_parser(
        "list-regions",
        help="List all available GCP regions.",
    )
    regions_parser.set_defaults(func=lambda args, proj, comp: list_regions(proj, comp))

    # List-full command
    list_full_parser = subparsers.add_parser("list-json", help="List full JSON info about all VM instances.")
    list_full_parser.set_defaults(func=lambda args, proj, comp: json.dump(list_machines_full(proj, comp), fp=sys.stdout))

    # Show-input-sample command
    sample_parser = subparsers.add_parser(
        "show-input-sample",
        help="Show a fake 10-line sample of the input expected by create.",
    )
    sample_parser.set_defaults(func=lambda args, proj, comp: show_input_sample())

    # Create command
    create_parser = subparsers.add_parser("create", help="Create VM instances from a file.")
    add_wait_flag(create_parser)
    create_parser.set_defaults(func=create_command)

    # Stop-all command
    stop_parser = subparsers.add_parser("stop", help="Stop all running VM instances.")
    add_wait_flag(stop_parser)
    stop_parser.set_defaults(func=lambda args, proj, comp: stop_all_machines(proj, comp, args.wait_mode))

    # Delete-all command
    delete_parser = subparsers.add_parser("delete", help="Delete all VM instances.")
    add_wait_flag(delete_parser)
    delete_parser.set_defaults(func=lambda args, proj, comp: delete_all_machines(proj, comp, args.wait_mode))

    args = parser.parse_args()
    credentials, project_id = google.auth.default()
    require_default_account(credentials)
    compute = get_compute_client()
    args.func(args, project_id, compute)

if __name__ == "__main__":
    main()

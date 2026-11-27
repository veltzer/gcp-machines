#!/usr/bin/env python
"""
Manage compute instances in a GCP project.
This script provides a command-line interface to create, list, stop, continue,
and delete virtual machines, and to reach them over ssh (connect, tunnel).
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import typing
from argparse import ArgumentParser
from collections.abc import Callable
from typing import Any

import google.auth
from googleapiclient import discovery
from googleapiclient.errors import HttpError

# The machine spec is shared with the web app, which lives in src/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import provision

# Always read the student list from data.gi/students.txt at the repo root.
# Each line is "<owner-name> [email]"; this script only uses the owner name,
# the email column is used by scripts/iap.py for web access grants.
STUDENTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data.gi",
    "students.txt",
)

def read_students() -> list[tuple[str, str | None]]:
    """
    Reads the students file and returns a list of (owner, email) pairs,
    where email is None when a line has only the owner name. Blank lines and
    lines starting with # are ignored.
    """
    if not os.path.exists(STUDENTS_FILE):
        sys.exit(
            f"Missing {STUDENTS_FILE}\n"
            "Create it with one student per line: <owner-name> [email]\n"
            "(see the show-input-sample command). It is git-ignored and stays\n"
            "out of the public repository."
        )
    students = []
    with open(STUDENTS_FILE, encoding="utf-8") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            students.append((parts[0], parts[1] if len(parts) > 1 else None))
    return students

# The user the ssh-keys metadata is created for, and so the one connect and
# tunnel log in as by default.
DEFAULT_USER = provision.DEFAULT_USER

# The port test-port checks when none is given: the ssh port every machine is
# expected to answer on, and the one connect and tunnel rely on.
DEFAULT_PORT = 22

# How long test-port waits for a connection before calling the port closed.
DEFAULT_PORT_TIMEOUT = 5.0

ALLOWED_ZONES = provision.ALLOWED_ZONES
MACHINE_TYPE = provision.MACHINE_TYPE
MACHINE_VCPUS = provision.MACHINE_VCPUS
MACHINE_QUOTA_METRICS = provision.MACHINE_QUOTA_METRICS
MAX_RUN_SECONDS = provision.MAX_RUN_SECONDS
RUN_LIMIT = provision.RUN_LIMIT

def require_default_account(credentials: Any) -> None:
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

def get_compute_client() -> typing.Any:
    """Initializes and returns a Compute Engine API client."""
    credentials, _ = google.auth.default()
    return discovery.build("compute", "v1", credentials=credentials)

def get_all_instances(project_id: str, compute: Any):
    """
    Returns the raw list of all instances in the project.
    """
    all_instances: list[dict[str, Any]] = []
    request = compute.instances().aggregatedList(project=project_id)
    while request is not None:
        response = request.execute()
        for instances_data in response["items"].values():
            instances = instances_data.get("instances", [])
            all_instances.extend(instances)
        request = compute.instances().aggregatedList_next(
            previous_request=request, previous_response=response
        )
    return all_instances

def list_machines_full(project_id: str, compute: Any):
    """
    Lists all instances in the project, returning the full JSON info for each.
    """
    return get_all_instances(project_id, compute)

def list_machines(project_id: str, compute: Any):
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

def print_machines_table(data: list[dict[str, Any]]) -> None:
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

def wait_for_operation(project_id: str, compute: Any, zone: str, op_name: str, action: str = "finish") -> None:
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

def wait_for_operations(project_id: str, compute: Any, operations: list[tuple[str, str]], action: str = "finish") -> None:
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

def wait_for_operation_quiet(project_id: str, compute: Any, zone: str, op_name: str) -> None:
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

def get_secrets_client() -> typing.Any:
    """Initializes and returns a Secret Manager API client."""
    credentials, _ = google.auth.default()
    return discovery.build("secretmanager", "v1", credentials=credentials)

def delete_machine_keys(project_id: str, secrets: Any, instance_names: list[str]) -> None:
    """ Deletes the key secrets of the given instances, where they exist. """
    for name in instance_names:
        if provision.delete_key(secrets, project_id, name):
            print(f"Deleted key of {name}.")

def stop_all_machines(project_id: str, compute: Any, wait_mode: str) -> None:
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

def has_run_limit(instance: dict[str, Any]):
    """ whether the instance already carries RUN_LIMIT """
    scheduling = instance.get("scheduling", {})
    return (
        str(scheduling.get("maxRunDuration", {}).get("seconds")) == str(MAX_RUN_SECONDS)
        and scheduling.get("instanceTerminationAction") == RUN_LIMIT["instanceTerminationAction"]
    )

def set_limit_all_machines(project_id: str, compute: Any, wait_mode: str) -> None:
    """
    Applies RUN_LIMIT to every machine that does not carry it yet, for
    machines created before it existed. Compute Engine only changes the
    scheduling of a stopped machine, so running or suspended ones are
    skipped and reported; stop them (the stop command) and run this again.
    The rest of each machine's scheduling is kept as it is.
    """
    operations = []
    skipped = []
    for instance in get_all_instances(project_id, compute):
        name = instance["name"]
        zone_name = instance["zone"].split("/")[-1]
        if has_run_limit(instance):
            print(f"Skipping {name} in {zone_name} (already limited)")
            continue
        if instance["status"] != "TERMINATED":
            print(f"Skipping {name} in {zone_name} (status: {instance['status']}, must be stopped)")
            skipped.append(name)
            continue
        print(f"Limiting {name} in {zone_name} to {MAX_RUN_SECONDS // 3600}h of running...")
        operation = compute.instances().setScheduling(
            project=project_id, zone=zone_name, instance=name,
            body={**instance.get("scheduling", {}), **RUN_LIMIT},
        ).execute()
        if wait_mode == WAIT_EACH:
            wait_for_operation(project_id, compute, zone_name, operation["name"], "be limited")
        else:
            operations.append((zone_name, operation["name"]))

    if wait_mode == WAIT_ALL:
        wait_for_operations(project_id, compute, operations, "be limited")
    if skipped:
        sys.exit(
            f"Not limited (not stopped): {', '.join(skipped)}.\n"
            "Stop them (python scripts/machines.py stop) and run set-limit again."
        )

def continue_all_machines(project_id: str, compute: Any, wait_mode: str) -> None:
    """
    Continues (starts) all stopped compute instances in the project.

    wait_mode (WAIT_NONE / WAIT_EACH / WAIT_ALL) controls how start operations
    are waited on; see the WAIT_* constants.
    """
    instances = compute.instances().aggregatedList(project=project_id).execute()
    operations = []
    for zone, zone_data in instances.get("items", {}).items():
        zone_name = zone.split("/")[-1]
        for instance in zone_data.get("instances", []):
            if instance["status"] == "TERMINATED":
                print(f"Continuing {instance['name']} in {zone_name}...")
                operation = compute.instances().start(
                    project=project_id, zone=zone_name, instance=instance["name"]
                ).execute()
                if wait_mode == WAIT_EACH:
                    wait_for_operation(project_id, compute, zone_name, operation["name"], "come up")
                else:
                    operations.append((zone_name, operation["name"]))
            else:
                print(f"Skipping {instance['name']} in {zone_name} (status: {instance['status']})")

    if wait_mode == WAIT_ALL:
        wait_for_operations(project_id, compute, operations, "come up")

def delete_all_machines(project_id: str, compute: Any, wait_mode: str) -> None:
    """
    Deletes all compute instances in the project, and their ssh key secrets.

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
    deleted = []
    for zone, zone_data in instances.get("items", {}).items():
        zone_name = zone.split("/")[-1]
        for instance in zone_data.get("instances", []):
            deleted.append(instance["name"])
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
    # the keys go with their machines: a later machine of the same name gets a new one
    delete_machine_keys(project_id, get_secrets_client(), deleted)

# Cache of per-region CPU quota limit, keyed by region name. Populated lazily
# by zone_machine_limit so zones sharing a region don't re-fetch it.
_REGION_CPU_LIMIT_CACHE: dict[str, int | None] = {}

def zone_machine_limit(project_id: str, compute: Any, zone: str) -> int | None:
    """
    Returns the absolute cap on how many machines of our standard machine
    type can be created in a zone, based on the region's CPU quotas, or None
    if the region has none of the MACHINE_QUOTA_METRICS quotas.

    CPU quota in GCP is per-region, so each zone is mapped to its region
    (e.g. "us-central1-a" -> "us-central1"). The cap is the smallest
    MACHINE_QUOTA_METRICS limit // vCPUs-per-machine, regardless of current
    usage.
    """
    region = zone.rsplit("-", 1)[0]
    if region not in _REGION_CPU_LIMIT_CACHE:
        region_info = compute.regions().get(project=project_id, region=region).execute()
        limits = [
            int(q["limit"])
            for q in region_info.get("quotas", [])
            if q["metric"] in MACHINE_QUOTA_METRICS
        ]
        _REGION_CPU_LIMIT_CACHE[region] = min(limits) if limits else None
    cpu_limit = _REGION_CPU_LIMIT_CACHE[region]
    return None if cpu_limit is None else cpu_limit // MACHINE_VCPUS

def list_regions(project_id: str, compute: Any) -> None:
    """
    Lists and prints the names of all available Google Cloud regions.
    """
    regions = compute.regions().list(project=project_id).execute()
    for region in regions["items"]:
        print(region["name"])

def machine_limits(project_id: str, compute: Any) -> None:
    """
    For every zone in the project, prints the absolute cap on how many
    machines of our standard machine type can be created there.
    """
    # Gather every zone in the project.
    zone_names = []
    request = compute.zones().list(project=project_id)
    while request is not None:
        response = request.execute()
        zone_names.extend(zone["name"] for zone in response.get("items", []))
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

def show_input_sample() -> None:
    """
    Prints a fake 10-line sample of the input expected in the students file,
    so the user can see what a valid file looks like. Each line is an owner
    name optionally followed by the student's email (this script uses only
    the name; the email is used by scripts/iap.py). Zones are assigned
    automatically at create time.
    """
    print("# <owner-name> [email]; # starts a comment")
    owners = [
        "alice", "bob", "carol", "dave", "eve",
        "frank", "grace", "heidi", "ivan", "judy",
    ]
    for owner in owners:
        print(f"{owner} {owner}@gmail.com")

def create_command(args: argparse.Namespace, project_id: str, compute: Any) -> None:
    """
    Creates a VM for every owner in the student list who has none yet (their
    machine number 1), allocating owners to zones up to each zone's machine
    limit, and waits according to args.wait_mode. Re-runnable: adding a
    student and running create again creates just that one. More machines
    per student are made by the students themselves in the web app, up to
    the limit an admin set them there.
    """
    owners = [owner for owner, _email in read_students()]

    # Check owners BEFORE creating anything, so we fail fast instead of
    # partway through creating machines.
    seen = set()
    duplicates = set()
    for owner in owners:
        if owner in seen:
            duplicates.add(owner)
        seen.add(owner)
    if duplicates:
        raise ValueError(
            f"Duplicate owner(s) in {STUDENTS_FILE}: "
            f"{', '.join(sorted(duplicates))}"
        )
    invalid = [owner for owner in owners if provision.instance_name(owner, 1) is None]
    if invalid:
        raise ValueError(f"Owner(s) that make no valid machine name: {', '.join(invalid)}")

    # Owners who have a machine already (made here or by them in the web
    # app) are skipped; their machines still count against each zone's limit.
    existing = get_all_instances(project_id, compute)
    have = {instance.get("labels", {}).get("owner") for instance in existing}
    used = {}
    for instance in existing:
        zone = instance["zone"].split("/")[-1]
        used[zone] = used.get(zone, 0) + 1
    for owner in owners:
        if owner in have:
            print(f"Skipping {owner} (has a machine)")
    owners = [owner for owner in owners if owner not in have]

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
        room = max(0, limit - used.get(zone, 0))
        take, remaining = remaining[:room], remaining[room:]
        assignments.extend((owner, zone) for owner in take)
    if remaining:
        raise ValueError(
            f"Not enough capacity in {', '.join(ALLOWED_ZONES)} for {len(owners)} more machine(s). "
            f"Owner(s) left unassigned: {', '.join(remaining)}"
        )

    # Each machine gets its own key pair, stored in Secret Manager, from
    # where the web app hands it out and connect and tunnel fetch it.
    secrets = get_secrets_client()
    operations = []
    for owner, zone in assignments:
        name = provision.instance_name(owner, 1)
        print(f"Creating {name} in {zone} for {owner}...")
        _name, op_name = provision.create(compute, secrets, project_id, owner, 1, zone)
        if args.wait_mode == WAIT_EACH:
            wait_for_operation(project_id, compute, zone, op_name, "come up")
            print(f"Instance {name} created successfully.")
        elif args.wait_mode == WAIT_ALL:
            operations.append((zone, op_name))
    if args.wait_mode == WAIT_ALL:
        wait_for_operations(project_id, compute, operations, "come up")

# The connect and tunnel commands never write an IP down: a machine is
# identified by the "owner" label it was created with, and its external IP is
# read live from the Compute API at the moment of connecting. Suspending and
# resuming a machine changes its IP, so anything cached would be stale.

def add_target_arguments(subparser: ArgumentParser) -> None:
    """
    Adds the arguments shared by every command that ssh-es to a machine: which
    owner to reach, as which user, and with which key.
    """
    subparser.add_argument("owner", help="Owner name of the machine, as in data.gi/students.txt.")
    subparser.add_argument(
        "--user",
        default=DEFAULT_USER,
        help=f"User to log in as (default: {DEFAULT_USER}).",
    )
    subparser.add_argument(
        "--key",
        default=None,
        help="Private key to use (default: the machine's own key, fetched from Secret Manager).",
    )

def run_ssh(project_id: str, name: str, key: str, ssh_args: list[str]):
    """
    Runs ssh with the given arguments after "-i <key>" and exits with its
    status. Without --key, the machine's own private key is fetched from
    Secret Manager into a private temporary directory, which is removed
    again when ssh exits; the key is never kept on disk.
    """
    if key:
        sys.exit(call_ssh(os.path.expanduser(key), ssh_args))
    try:
        private_key = provision.read_key(get_secrets_client(), project_id, name)
    except HttpError as error:
        if error.resp.status != 404:
            raise
        sys.exit(f"Machine {name} has no key in Secret Manager ({provision.KEY_SECRET_PREFIX}{name}).")
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, name)
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(private_key)
        status = call_ssh(path, ssh_args)
    sys.exit(status)

def call_ssh(key_path: str, ssh_args: list[str]) -> int:
    """
    Runs "ssh -i <key_path> <ssh_args>" and returns its exit status; ctrl-c,
    which ends a tunnel, ends ssh and returns 130 instead of a traceback.
    """
    try:
        return subprocess.call(["ssh", "-i", key_path, *ssh_args])
    except KeyboardInterrupt:
        return 130

def instance_ip(instance: dict[str, Any]):
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

def find_by_owner(project_id: str, compute: Any, owner: str):
    """
    Returns the instances labelled with the given owner.
    """
    return [
        instance
        for instance in get_all_instances(project_id, compute)
        if instance.get("labels", {}).get("owner") == owner
    ]

def owner_machine(project_id: str, compute: Any, owner: str):
    """
    Returns (instance name, current external IP) of the machine belonging to
    an owner.

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
    return instance["name"], ip

def strip_separator(extra_args: list[str]):
    """
    Drops the leading "--" argparse.REMAINDER keeps when the user separates
    pass-through arguments with it, so it is not forwarded to ssh.
    """
    if extra_args and extra_args[0] == "--":
        return extra_args[1:]
    return extra_args

def parse_ports(spec: str) -> tuple[int, int]:
    """
    Parses a port specification into a (local_port, remote_port) pair.

    "5602:5601" forwards local 5602 to remote 5601; a bare "5601" uses the
    same port on both ends.
    """
    parts = spec.split(":")
    if len(parts) > 2:
        raise argparse.ArgumentTypeError(
            f"Bad port specification '{spec}'; expected <local>:<remote> or <port>."
        )
    ports = []
    for part in parts:
        if not part.isdigit() or not 1 <= int(part) <= 65535:
            raise argparse.ArgumentTypeError(
                f"Bad port '{part}' in '{spec}'; expected a number in 1-65535."
            )
        ports.append(int(part))
    return (ports[0], ports[-1])

def connect_command(args: argparse.Namespace, project_id: str, compute: Any):
    """
    SSH into a student's machine by owner name.
    """
    name, ip = owner_machine(project_id, compute, args.owner)
    print(f"Connecting to {args.owner} at {ip}...", file=sys.stderr)
    run_ssh(project_id, name, args.key, [f"{args.user}@{ip}", *strip_separator(args.ssh_args)])

def test_port_command(args: argparse.Namespace, project_id: str, compute: Any) -> None:
    """
    Tests whether a port on a student's machine accepts TCP connections,
    looking the machine up by owner name. Exits non-zero when it does not, so
    the command can be used in a shell test.

    This says nothing about what is listening, only that something accepted
    the connection; for ssh (the default port) that is the interesting
    question, since a freshly created or just-continued machine answers on 22
    only once it has finished booting.
    """
    _name, ip = owner_machine(project_id, compute, args.owner)
    print(
        f"Testing port {args.port} on {args.owner} ({ip})...",
        file=sys.stderr,
    )
    try:
        with socket.create_connection((ip, args.port), timeout=args.timeout):
            pass
    except OSError as exception:
        print(f"{ip}:{args.port} CLOSED ({exception})")
        sys.exit(1)
    print(f"{ip}:{args.port} OPEN")

def tunnel_command(args: argparse.Namespace, project_id: str, compute: Any):
    """
    Opens an SSH port-forward to a student's machine by owner name.
    """
    name, ip = owner_machine(project_id, compute, args.owner)
    local_port, remote_port = args.ports
    print(
        f"Forwarding localhost:{local_port} to {args.host}:{remote_port} "
        f"on {args.owner} ({ip}); ctrl-c to stop.",
        file=sys.stderr,
    )
    run_ssh(project_id, name, args.key, ["-N", "-L", f"{local_port}:{args.host}:{remote_port}", f"{args.user}@{ip}"])

def add_wait_flag(subparser: ArgumentParser) -> None:
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

Command = Callable[[argparse.Namespace, str, Any], object]


def command(parser: argparse.ArgumentParser, run: Command) -> None:
    """ Sets what a subcommand runs. """
    parser.set_defaults(func=run)


def main() -> None:
    """Main entry point and command-line parser."""
    parser = argparse.ArgumentParser(description="Manage GCP compute instances.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # List command
    list_parser = subparsers.add_parser("list", help="List students and public IPs of all VM instances.")
    command(list_parser, lambda _args, proj, comp: print_machines_table(list_machines(proj, comp)))

    # Machine-limits command
    limits_parser = subparsers.add_parser(
        "machine-limits",
        help="Show how many machines can be created in each allowed zone.",
    )
    command(limits_parser, lambda _args, proj, comp: machine_limits(proj, comp))

    # List-regions command
    regions_parser = subparsers.add_parser(
        "list-regions",
        help="List all available GCP regions.",
    )
    command(regions_parser, lambda _args, proj, comp: list_regions(proj, comp))

    # List-full command
    list_full_parser = subparsers.add_parser("list-json", help="List full JSON info about all VM instances.")
    command(list_full_parser, lambda _args, proj, comp: json.dump(list_machines_full(proj, comp), fp=sys.stdout))

    # Show-input-sample command
    sample_parser = subparsers.add_parser(
        "show-input-sample",
        help="Show a fake 10-line sample of the input expected by create.",
    )
    command(sample_parser, lambda _args, _proj, _comp: show_input_sample())

    # Create command
    create_parser = subparsers.add_parser("create", help="Create a VM for every student in the file who has none.")
    add_wait_flag(create_parser)
    create_parser.set_defaults(func=create_command)

    # Stop-all command
    stop_parser = subparsers.add_parser("stop", help="Stop all running VM instances.")
    add_wait_flag(stop_parser)
    command(stop_parser, lambda args, proj, comp: stop_all_machines(proj, comp, args.wait_mode))

    # Continue-all command
    continue_parser = subparsers.add_parser("continue", help="Continue (start) all stopped VM instances.")
    add_wait_flag(continue_parser)
    command(continue_parser, lambda args, proj, comp: continue_all_machines(proj, comp, args.wait_mode))

    # Set-limit command
    set_limit_parser = subparsers.add_parser(
        "set-limit",
        help=f"Make every stopped VM turn itself off {MAX_RUN_SECONDS // 3600}h after each start.",
    )
    add_wait_flag(set_limit_parser)
    command(set_limit_parser, lambda args, proj, comp: set_limit_all_machines(proj, comp, args.wait_mode))

    # Delete-all command
    delete_parser = subparsers.add_parser("delete", help="Delete all VM instances.")
    add_wait_flag(delete_parser)
    command(delete_parser, lambda args, proj, comp: delete_all_machines(proj, comp, args.wait_mode))

    # Connect command
    connect_parser = subparsers.add_parser(
        "connect",
        help="SSH into a student's machine by owner name.",
        description=(
            "SSH into a student's machine by owner name. The machine is looked "
            "up by its 'owner' label and reached at its current external IP, so "
            "a machine that was stopped and continued (and therefore got a new "
            "IP) is reached without editing anything.\n\n"
            "    python scripts/machines.py connect mark\n"
            "    python scripts/machines.py connect mark -- -v"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_target_arguments(connect_parser)
    connect_parser.add_argument(
        "ssh_args",
        nargs=argparse.REMAINDER,
        help="Extra arguments passed to ssh (put them after a -- separator).",
    )
    connect_parser.set_defaults(func=connect_command)

    # Test-port command
    test_port_parser = subparsers.add_parser(
        "test-port",
        help="Test whether a port on a student's machine is open.",
        description=(
            "Test whether a port on a student's machine accepts TCP "
            "connections. The machine is looked up by its 'owner' label and "
            "reached at its current external IP. Exits 0 when the port is "
            "open and 1 when it is not, so it can be used in a shell test.\n\n"
            f"    # the ssh port ({DEFAULT_PORT}) by default\n"
            "    python scripts/machines.py test-port mark\n\n"
            "    # any other port\n"
            "    python scripts/machines.py test-port mark --port 8080"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    test_port_parser.add_argument(
        "owner",
        help="Owner name of the machine, as in data.gi/students.txt.",
    )
    test_port_parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to test (default: {DEFAULT_PORT}, ssh).",
    )
    test_port_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_PORT_TIMEOUT,
        help=f"Seconds to wait for the connection (default: {DEFAULT_PORT_TIMEOUT}).",
    )
    test_port_parser.set_defaults(func=test_port_command)

    # Tunnel command
    tunnel_parser = subparsers.add_parser(
        "tunnel",
        help="Open an SSH port-forward to a student's machine by owner name.",
        description=(
            "Open an SSH port-forward to a student's machine by owner name.\n\n"
            "    # local 5602 -> the machine's 5601\n"
            "    python scripts/machines.py tunnel mark 5602:5601\n\n"
            "    # same port on both ends\n"
            "    python scripts/machines.py tunnel mark 5601"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_target_arguments(tunnel_parser)
    tunnel_parser.add_argument(
        "ports",
        type=parse_ports,
        help="Ports to forward: <local>:<remote>, or a single port for both ends.",
    )
    tunnel_parser.add_argument(
        "--host",
        default="localhost",
        help="Host the remote side connects to, as seen from the machine (default: localhost).",
    )
    tunnel_parser.set_defaults(func=tunnel_command)

    args = parser.parse_args()
    credentials, project_id = google.auth.default()
    require_default_account(credentials)
    compute = get_compute_client()
    args.func(args, project_id, compute)

if __name__ == "__main__":
    main()

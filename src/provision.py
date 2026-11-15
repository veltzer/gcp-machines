"""
What a student machine is and how one comes to be: the one place the
machine spec, its naming and its ssh key handling live. Used by the web app
(a student creates their own machine) and by scripts/machines.py (an admin
creates them in bulk).

Every API call takes an optional http: the web app passes its thread's own
connection (see main.api_http), the command line passes none.
"""

import base64
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from googleapiclient.errors import HttpError

# The only zones machines are created in, tried in this order.
ALLOWED_ZONES = ("us-central1-a", "us-east1-c")

# N2 (Intel) rather than E2 because E2 does not support nested
# virtualization, which the machines need to run KVM guests (qemu, libvirt).
MACHINE_TYPE = "n2-standard-2"
MACHINE_VCPUS = 2

# The regional quota metrics a machine of MACHINE_TYPE counts against: the
# general CPUS quota and the per-series N2_CPUS quota. The tighter one wins.
MACHINE_QUOTA_METRICS = ("CPUS", "N2_CPUS")

# The Ubuntu 22.04 LTS family, so every machine starts from the latest
# patched image (a pinned dated image gets deprecated and deleted by Google).
BOOT_IMAGE = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
DISK_GB = 10

# The user the ssh-keys metadata is made for, and so the one students log in as.
DEFAULT_USER = "ubuntu"

# Every machine turns itself off this long after it starts. Compute Engine
# enforces it (scheduling.maxRunDuration): the countdown starts whenever the
# machine enters RUNNING and is cleared when it stops or suspends. When it
# runs out the machine is stopped, not deleted: the disk is kept.
MAX_RUN_SECONDS = 4 * 60 * 60
RUN_LIMIT = {
    "maxRunDuration": {"seconds": str(MAX_RUN_SECONDS)},
    "instanceTerminationAction": "STOP",
}

# Each machine's private ssh key lives only in Secret Manager, in the secret
# KEY_SECRET_PREFIX + <instance name>, from where the web app hands it to the
# machine's student.
KEY_SECRET_PREFIX = "machine-key-"

# What Compute Engine allows in instance and zone names.
RESOURCE_NAME = re.compile(r"[a-z]([-a-z0-9]{0,61}[a-z0-9])?")


def instance_name(owner):
    """
    The name of the owner's machine, or None when the owner label cannot
    form a valid instance name. One name per owner is what keeps it to one
    machine per student: a second insert of the same name in the same zone
    fails, however the requests race.
    """
    name = "machine-" + owner.replace("_", "-")
    return name if RESOURCE_NAME.fullmatch(name) else None


def generate_key_pair(comment):
    """ a new ed25519 ssh key pair: (private key in OpenSSH format, public key line ending in comment) """
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode()
    return private, f"{public} {comment}"


def store_key(secrets, project_id, name, owner, private_key, http=None):
    """ stores a machine's private key as the newest version of its secret, creating the secret on first use """
    secret_id = KEY_SECRET_PREFIX + name
    parent = f"projects/{project_id}"
    try:
        secrets.projects().secrets().create(
            parent=parent,
            secretId=secret_id,
            body={
                "replication": {"automatic": {}},
                "labels": {"owner": owner, "instance": name},
            },
        ).execute(http=http)
    except HttpError as error:
        if error.resp.status != 409:  # 409: it exists already (an earlier machine's), add a version
            raise
    secrets.projects().secrets().addVersion(
        parent=f"{parent}/secrets/{secret_id}",
        body={"payload": {"data": base64.b64encode(private_key.encode()).decode()}},
    ).execute(http=http)


def delete_key(secrets, project_id, name, http=None):
    """ deletes a machine's key secret; True if there was one """
    try:
        secrets.projects().secrets().delete(
            name=f"projects/{project_id}/secrets/{KEY_SECRET_PREFIX}{name}",
        ).execute(http=http)
    except HttpError as error:
        if error.resp.status != 404:
            raise
        return False
    return True


def instance_body(name, zone, owner, ssh_keys):
    """ the insert request for a machine; ssh_keys are the public key lines its ubuntu user accepts """
    return {
        "name": name,
        "machineType": f"zones/{zone}/machineTypes/{MACHINE_TYPE}",
        "networkInterfaces": [{
            "network": "global/networks/default",
            "accessConfigs": [{"name": "External NAT", "type": "ONE_TO_ONE_NAT"}],
        }],
        "metadata": {"items": [{
            "key": "ssh-keys",
            "value": "\n".join(f"{DEFAULT_USER}:{key}" for key in ssh_keys),
        }]},
        "disks": [{
            "boot": True,
            "autoDelete": True,
            "initializeParams": {"sourceImage": BOOT_IMAGE, "diskSizeGb": DISK_GB},
        }],
        "labels": {"owner": owner},
        "scheduling": dict(RUN_LIMIT),
        "advancedMachineFeatures": {"enableNestedVirtualization": True},
    }


def region_of(zone):
    """ "us-central1-a" -> "us-central1" """
    return zone.rsplit("-", 1)[0]


def free_machines(compute, project_id, zone, http=None):
    """
    How many more machines can run in the zone's region right now, by the
    region's CPU quotas (limit minus current usage), or None when the region
    has none of MACHINE_QUOTA_METRICS.
    """
    region = compute.regions().get(project=project_id, region=region_of(zone)).execute(http=http)
    free = [
        int(float(quota["limit"]) - float(quota.get("usage", 0)))
        for quota in region.get("quotas", [])
        if quota["metric"] in MACHINE_QUOTA_METRICS
    ]
    return min(free) // MACHINE_VCPUS if free else None


def pick_zone(compute, project_id, http=None):
    """ the first allowed zone whose region has room for one more running machine, or None """
    for zone in ALLOWED_ZONES:
        free = free_machines(compute, project_id, zone, http=http)
        if free is None or free > 0:
            return zone
    return None


def create(compute, secrets, project_id, owner, zone, extra_keys=(), http=None):
    """
    Creates the owner's machine in zone with a new key pair of its own, and
    returns (instance name, insert operation name). Does not wait for it to
    come up.

    The insert goes first and the key is stored after it: of two racing
    requests for the same owner the second insert fails (same name, same
    zone) before it can replace the first one's key. Should storing the key
    fail, the machine is deleted again rather than left without a key.
    extra_keys are more public key lines its ubuntu user accepts (the
    admin's, from the command line).
    """
    name = instance_name(owner)
    if name is None:
        raise ValueError(f"owner label {owner!r} does not make a valid machine name")
    private_key, public_key = generate_key_pair(f"{owner}@{name}")
    body = instance_body(name, zone, owner, [*extra_keys, public_key])
    operation = compute.instances().insert(project=project_id, zone=zone, body=body).execute(http=http)
    try:
        store_key(secrets, project_id, name, owner, private_key, http=http)
    except Exception:
        compute.instances().delete(project=project_id, zone=zone, instance=name).execute(http=http)
        raise
    return name, operation["name"]

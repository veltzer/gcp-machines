#!/usr/bin/env python
"""
SSH into a student's machine by owner name.

Looks the machine up by its "owner" label and uses its current external IP,
so a machine that was suspended and resumed (and therefore got a new IP) is
reached without editing anything.

    python scripts/connect.py mark
    python scripts/connect.py mark -- -v
"""

import argparse
import os
import sys
import google.auth
from googleapiclient import discovery

import lib_machines

def main():
    """Main entry point and command-line parser."""
    parser = argparse.ArgumentParser(description="SSH into a student's machine by owner name.")
    lib_machines.add_target_arguments(parser)
    parser.add_argument(
        "ssh_args",
        nargs=argparse.REMAINDER,
        help="Extra arguments passed to ssh (put them after a -- separator).",
    )
    args = parser.parse_args()

    credentials, project_id = google.auth.default()
    compute = discovery.build("compute", "v1", credentials=credentials)
    ip = lib_machines.owner_ip(project_id, compute, args.owner)

    command = [
        "ssh",
        "-i", lib_machines.ssh_key_path(args.key),
        f"{args.user}@{ip}",
    ] + lib_machines.strip_separator(args.ssh_args)
    print(f"Connecting to {args.owner} at {ip}...", file=sys.stderr)
    os.execvp(command[0], command)

if __name__ == "__main__":
    main()

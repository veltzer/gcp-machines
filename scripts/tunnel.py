#!/usr/bin/env python
"""
Open an SSH port-forward to a student's machine by owner name.

Looks the machine up by its "owner" label and uses its current external IP,
so a machine that was suspended and resumed (and therefore got a new IP) is
reached without editing anything.

    # local 5602 -> the machine's 5601
    python scripts/tunnel.py mark 5602:5601

    # same port on both ends
    python scripts/tunnel.py mark 5601
"""

import argparse
import os
import sys
import google.auth
from googleapiclient import discovery

import lib_machines

def parse_ports(spec):
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

def main():
    """Main entry point and command-line parser."""
    parser = argparse.ArgumentParser(
        description="Open an SSH port-forward to a student's machine by owner name.",
    )
    lib_machines.add_target_arguments(parser)
    parser.add_argument(
        "ports",
        type=parse_ports,
        help="Ports to forward: <local>:<remote>, or a single port for both ends.",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Host the remote side connects to, as seen from the machine (default: localhost).",
    )
    args = parser.parse_args()

    credentials, project_id = google.auth.default()
    compute = discovery.build("compute", "v1", credentials=credentials)
    ip = lib_machines.owner_ip(project_id, compute, args.owner)

    local_port, remote_port = args.ports
    command = [
        "ssh",
        "-N",
        "-i", lib_machines.ssh_key_path(args.key),
        "-L", f"{local_port}:{args.host}:{remote_port}",
        f"{args.user}@{ip}",
    ]
    print(
        f"Forwarding localhost:{local_port} to {args.host}:{remote_port} "
        f"on {args.owner} ({ip}); ctrl-c to stop.",
        file=sys.stderr,
    )
    os.execvp(command[0], command)

if __name__ == "__main__":
    main()

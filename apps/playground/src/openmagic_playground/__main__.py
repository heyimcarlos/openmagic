from __future__ import annotations

import argparse
import json

from example_insurance.reset import reset_synthetic_deployment

from openmagic_playground import safety_manifest


def main() -> None:
    parser = argparse.ArgumentParser(prog="openmagic-playground")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("manifest", help="print the synthetic safety contract")
    reset = commands.add_parser("reset", help="rebuild an explicitly synthetic database")
    reset.add_argument("--database-url", required=True)
    reset.add_argument("--accept-destructive-reset", action="store_true")
    arguments = parser.parse_args()
    if arguments.command == "manifest":
        print(json.dumps(safety_manifest().as_dict(), sort_keys=True))
        return
    if not arguments.accept_destructive_reset:
        parser.error("--accept-destructive-reset is required")
    reset_synthetic_deployment(arguments.database_url)
    print(json.dumps({"reset": "complete", "synthetic": True}, sort_keys=True))


if __name__ == "__main__":
    main()

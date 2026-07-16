from __future__ import annotations

import argparse
import json

from openmagic_playground import process_controls, safety_manifest


def main() -> None:
    parser = argparse.ArgumentParser(prog="openmagic-playground")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("manifest", help="print the synthetic safety contract")
    commands.add_parser("controls", help="print explicit local role process controls")
    arguments = parser.parse_args()
    if arguments.command == "manifest":
        print(json.dumps(safety_manifest().as_dict(), sort_keys=True))
        return
    if arguments.command == "controls":
        print(json.dumps(process_controls().as_dict(), sort_keys=True))
        return


if __name__ == "__main__":
    main()

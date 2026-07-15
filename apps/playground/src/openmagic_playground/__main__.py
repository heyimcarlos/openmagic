from __future__ import annotations

import argparse
import json

from openmagic_playground import safety_manifest


def main() -> None:
    parser = argparse.ArgumentParser(prog="openmagic-playground")
    parser.add_argument("--safety-manifest", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(safety_manifest().as_dict(), sort_keys=True))


if __name__ == "__main__":
    main()

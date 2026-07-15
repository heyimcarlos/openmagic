from __future__ import annotations

import argparse

import uvicorn
from example_insurance.readiness import verify_application_ready
from openmagic_runtime.evidence import inspect_runtime_database

from openmagic_api.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="openmagic-api")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    arguments = parser.parse_args()
    inspect_runtime_database(arguments.database_url)
    verify_application_ready(arguments.database_url)
    uvicorn.run(
        create_app(database_url=arguments.database_url),
        host=arguments.host,
        port=arguments.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()

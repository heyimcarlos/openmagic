"""Explicit Workflow Worker and Delivery Worker process entry points."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Literal

from openmagic_runtime.evidence import inspect_runtime_database

WorkerRole = Literal["workflow-worker", "delivery-worker"]


def _serve(*, role: WorkerRole, database_url: str, host: str, port: int, worker_id: str) -> None:
    inspect_runtime_database(database_url)

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            health = inspect_runtime_database(database_url).as_dict()
            health.update({"role": role, "worker_id": worker_id})
            payload = json.dumps(health).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    ThreadingHTTPServer((host, port), HealthHandler).serve_forever()


def _main(role: WorkerRole) -> None:
    parser = argparse.ArgumentParser(prog=f"openmagic-{role}")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--worker-id", required=True)
    arguments = parser.parse_args()
    _serve(
        role=role,
        database_url=arguments.database_url,
        host=arguments.host,
        port=arguments.port,
        worker_id=arguments.worker_id,
    )


def workflow_worker_main() -> None:
    _main("workflow-worker")


def delivery_worker_main() -> None:
    _main("delivery-worker")


__all__ = ["WorkerRole", "delivery_worker_main", "workflow_worker_main"]

"""Generic installed Worker process lifecycle."""

from __future__ import annotations

import json
import signal
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal

from openmagic_runtime.evidence import inspect_runtime_database

WorkerRole = Literal["workflow-worker", "delivery-worker"]


def serve_worker(
    *,
    role: WorkerRole,
    database_url: str,
    host: str,
    port: int,
    worker_id: str,
    tick: Callable[[threading.Event], object],
    polling_seconds: float = 0.05,
) -> None:
    inspect_runtime_database(database_url)
    stop = threading.Event()
    failures: list[str] = []

    def work_loop() -> None:
        while not stop.is_set():
            try:
                tick(stop)
                stop.wait(polling_seconds)
            except Exception as error:
                failures[:] = [type(error).__name__]
                stop.wait(polling_seconds)

    worker_thread = threading.Thread(target=work_loop, name=f"{role}-loop", daemon=True)
    worker_thread.start()

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            health = inspect_runtime_database(database_url).as_dict()
            health.update(
                {
                    "role": role,
                    "worker_id": worker_id,
                    "worker_failures": tuple(failures),
                }
            )
            payload = json.dumps(health).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), HealthHandler)
    previous_handlers: dict[int, Any] = {}

    def request_shutdown(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_shutdown)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
        worker_thread.join(timeout=max(1.0, polling_seconds * 4))
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


__all__ = ["WorkerRole", "serve_worker"]

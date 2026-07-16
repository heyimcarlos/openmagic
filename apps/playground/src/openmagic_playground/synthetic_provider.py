"""Process-controlled local email provider for synthetic playground scenarios."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BufferedWriter
from pathlib import Path
from types import TracebackType
from typing import Literal
from urllib.error import URLError
from urllib.request import urlopen

ProviderBehavior = Literal["success", "not_applied"]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@dataclass(frozen=True)
class SyntheticProviderRequest:
    logical_effect_id: str
    provider_request_id: str
    provider_process_id: int
    behavior: ProviderBehavior


class _ProviderHandler(BaseHTTPRequestHandler):
    server: _ProviderServer

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:
        if self.path == "/health":
            self._respond(200, {"status": "ready"})
            return
        self._respond(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/dispatch":
            self._respond(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict) or not isinstance(value.get("logical_effect_id"), str):
            self._respond(400, {"error": "invalid_dispatch"})
            return
        logical_effect_id = value["logical_effect_id"]
        provider_request_id = f"playground:{logical_effect_id}"
        with self.server.request_log.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "logical_effect_id": logical_effect_id,
                        "provider_request_id": provider_request_id,
                        "provider_process_id": os.getpid(),
                        "behavior": self.server.behavior,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
        classification = "applied" if self.server.behavior == "success" else "not_applied"
        self._respond(
            200 if classification == "applied" else 422,
            {
                "classification": classification,
                "provider_request_id": provider_request_id,
            },
        )

    def _respond(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ProviderServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        *,
        behavior: ProviderBehavior,
        request_log: Path,
    ) -> None:
        self.behavior = behavior
        self.request_log = request_log
        super().__init__(address, _ProviderHandler)


class SyntheticEmailProvider:
    """Own one local provider child and its observations."""

    def __init__(
        self,
        *,
        working_directory: Path,
        behavior: ProviderBehavior = "success",
        readiness_timeout: float = 10,
        shutdown_timeout: float = 5,
    ) -> None:
        self.working_directory = working_directory.resolve()
        self.behavior = behavior
        self.readiness_timeout = readiness_timeout
        self.shutdown_timeout = shutdown_timeout
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.request_log = self.working_directory / "requests.jsonl"
        self._process: subprocess.Popen[bytes] | None = None
        self._log: BufferedWriter | None = None

    def __enter__(self) -> SyntheticEmailProvider:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.stop()

    @property
    def pid(self) -> int:
        if self._process is None:
            raise RuntimeError("synthetic provider is not running")
        return self._process.pid

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("synthetic provider is already running")
        self.working_directory.mkdir(parents=True, exist_ok=True)
        self.request_log.unlink(missing_ok=True)
        self._log = (self.working_directory / "provider.log").open("ab")
        try:
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "openmagic_playground.synthetic_provider",
                    "serve",
                    "--port",
                    str(self.port),
                    "--behavior",
                    self.behavior,
                    "--request-log",
                    str(self.request_log),
                ],
                cwd=self.working_directory,
                env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"},
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + self.readiness_timeout
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    raise RuntimeError("synthetic provider exited before readiness")
                try:
                    with urlopen(f"{self.url}/health", timeout=0.5) as response:
                        if json.load(response) == {"status": "ready"}:
                            return
                except (OSError, URLError, ValueError):
                    time.sleep(0.02)
            raise TimeoutError("synthetic provider did not become ready")
        except BaseException as start_error:
            try:
                self.stop()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup(
                    "synthetic provider start and cleanup failed",
                    [start_error, cleanup_error],
                ) from start_error
            raise

    def stop(self) -> None:
        errors: list[Exception] = []
        process = self._process
        try:
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except Exception as error:
                    errors.append(error)
                try:
                    process.wait(timeout=self.shutdown_timeout)
                except subprocess.TimeoutExpired:
                    pass
                except Exception as error:
                    errors.append(error)
                if process.poll() is None:
                    try:
                        process.kill()
                    except Exception as error:
                        errors.append(error)
                if process.poll() is None:
                    try:
                        process.wait(timeout=self.shutdown_timeout)
                    except Exception as error:
                        errors.append(error)
            if process is not None and process.poll() is None:
                errors.append(RuntimeError(f"synthetic provider {process.pid} survived cleanup"))
        finally:
            if process is None or process.poll() is not None:
                self._process = None
            if self._log is not None:
                try:
                    self._log.close()
                except Exception as error:
                    errors.append(error)
                finally:
                    self._log = None
        if errors:
            raise ExceptionGroup("synthetic provider cleanup failed", errors)

    def requests(self) -> tuple[SyntheticProviderRequest, ...]:
        if not self.request_log.is_file():
            return ()
        return tuple(
            SyntheticProviderRequest(**json.loads(line))
            for line in self.request_log.read_text(encoding="utf-8").splitlines()
        )


def _serve(arguments: argparse.Namespace) -> None:
    _ProviderServer(
        ("127.0.0.1", arguments.port),
        behavior=arguments.behavior,
        request_log=arguments.request_log.resolve(),
    ).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", required=True, type=int)
    serve.add_argument("--behavior", choices=("success", "not_applied"), required=True)
    serve.add_argument("--request-log", required=True, type=Path)
    serve.set_defaults(handler=_serve)
    arguments = parser.parse_args()
    arguments.handler(arguments)


if __name__ == "__main__":
    main()


__all__ = ["SyntheticEmailProvider", "SyntheticProviderRequest"]

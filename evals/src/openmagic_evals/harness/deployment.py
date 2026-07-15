from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

from example_insurance.migrations import apply_migrations
from testcontainers.postgres import PostgresContainer

from openmagic_evals.harness._postgres import postgres_container

ProcessRole = Literal["api", "workflow-worker", "delivery-worker"]


@dataclass(frozen=True)
class ManagedProcess:
    role: ProcessRole
    pid: int
    health_url: str


@dataclass
class _RunningProcess:
    public: ManagedProcess
    process: subprocess.Popen[bytes]
    log_handle: object


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


class TestDeployment:
    """Own one PostgreSQL deployment and three installed OS processes."""

    __test__ = False

    def __init__(self, *, working_directory: Path, readiness_timeout: float = 30.0) -> None:
        self.working_directory = working_directory.resolve()
        self.readiness_timeout = readiness_timeout
        self.database_url = ""
        self.database_name = ""
        self.processes: tuple[ManagedProcess, ...] = ()
        self._container: PostgresContainer | None = None
        self._running: list[_RunningProcess] = []

    def __enter__(self) -> TestDeployment:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def start(self) -> None:
        if self._container is not None:
            raise RuntimeError("TestDeployment is already running")
        self.working_directory.mkdir(parents=True, exist_ok=True)
        database_name = f"openmagic_test_{uuid4().hex}"
        container = postgres_container(database_name=database_name)
        self._container = container
        try:
            container.start()
            self.database_url = container.get_connection_url(driver=None)
            self.database_name = database_name
            apply_migrations(self.database_url)
            self._start_process("api", "openmagic-api")
            self._start_process("workflow-worker", "openmagic-workflow-worker")
            self._start_process("delivery-worker", "openmagic-delivery-worker")
            self.processes = tuple(running.public for running in self._running)
            for process in self.processes:
                self._wait_ready(process)
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        for running in reversed(self._running):
            if running.process.poll() is None:
                running.process.terminate()
        deadline = time.monotonic() + 5
        for running in reversed(self._running):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                running.process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                running.process.kill()
                running.process.wait(timeout=5)
            close = getattr(running.log_handle, "close", None)
            if close is not None:
                close()
        self._running.clear()
        self.processes = ()
        if self._container is not None:
            self._container.stop()
            self._container = None

    def _start_process(self, role: ProcessRole, script_name: str) -> None:
        port = _free_port()
        script = Path(sys.executable).parent / script_name
        if not script.is_file():
            raise RuntimeError(f"installed process entry point is missing: {script_name}")
        worker_arguments = []
        if role != "api":
            worker_arguments = ["--worker-id", f"{role}-{uuid4().hex}"]
        command = [
            str(script),
            "--database-url",
            self.database_url,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            *worker_arguments,
        ]
        clean_environment = {
            "PATH": os.defpath,
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        log_path = self.working_directory / f"{role}.log"
        log_handle = log_path.open("wb")
        process = subprocess.Popen(
            command,
            cwd=self.working_directory,
            env=clean_environment,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._running.append(
            _RunningProcess(
                public=ManagedProcess(
                    role=role,
                    pid=process.pid,
                    health_url=f"http://127.0.0.1:{port}/health",
                ),
                process=process,
                log_handle=log_handle,
            )
        )

    def _wait_ready(self, process: ManagedProcess) -> None:
        deadline = time.monotonic() + self.readiness_timeout
        while time.monotonic() < deadline:
            running = next(item for item in self._running if item.public.pid == process.pid)
            exit_code = running.process.poll()
            if exit_code is not None:
                raise RuntimeError(
                    f"{process.role} exited before readiness with status {exit_code}"
                )
            try:
                with urlopen(process.health_url, timeout=1) as response:
                    payload = json.load(response)
                if payload.get("status") == "ready" and payload.get("role") == process.role:
                    return
            except (OSError, URLError, ValueError):
                time.sleep(0.05)
        raise TimeoutError(f"{process.role} did not become ready within {self.readiness_timeout}s")

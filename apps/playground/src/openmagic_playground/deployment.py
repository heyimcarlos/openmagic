"""Explicit process ownership for the local synthetic playground."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

from example_insurance.migrations import apply_migrations
from testcontainers.postgres import PostgresContainer

from openmagic_playground.reset import mark_synthetic_deployment, reset_synthetic_deployment

POSTGRES_IMAGE = "postgres@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
_VERIFICATION_SECRET = "openmagic-playground-synthetic-verification-secret"
ProcessRole = Literal["api", "workflow-worker", "delivery-worker"]
_SCRIPTS: dict[ProcessRole, str] = {
    "api": "openmagic-api",
    "workflow-worker": "openmagic-workflow-worker",
    "delivery-worker": "openmagic-delivery-worker",
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


@dataclass(frozen=True)
class ManagedProcess:
    role: ProcessRole
    pid: int
    health_url: str
    worker_id: str | None


@dataclass
class _RunningProcess:
    public: ManagedProcess
    process: subprocess.Popen[bytes]
    log_handle: object


class PlaygroundDeployment:
    """Own one synthetic PostgreSQL deployment and explicit OS processes."""

    def __init__(
        self,
        *,
        working_directory: Path,
        readiness_timeout: float = 30.0,
        email_provider_url: str | None = None,
        verification_code_secret: str | None = None,
        role_capacities: Mapping[ProcessRole, int] | None = None,
    ) -> None:
        self.working_directory = working_directory.resolve()
        self.readiness_timeout = readiness_timeout
        self.email_provider_url = email_provider_url
        self.verification_code_secret = verification_code_secret
        capacities = role_capacities or {
            "api": 1,
            "workflow-worker": 1,
            "delivery-worker": 1,
        }
        if set(capacities) != set(_SCRIPTS):
            raise ValueError("Process pools require explicit API, Workflow, and Delivery capacity")
        if any(type(value) is not int or value <= 0 for value in capacities.values()):
            raise ValueError("Initial process-pool capacities must be positive integers")
        self.role_capacities = dict(capacities)
        self.database_url = ""
        self.database_name = ""
        self.processes: tuple[ManagedProcess, ...] = ()
        self._container: PostgresContainer | None = None
        self._running: list[_RunningProcess] = []
        self._verification_secret_path = self.working_directory / "verification-code-secret"

    def __enter__(self) -> PlaygroundDeployment:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def start(self) -> tuple[ManagedProcess, ...]:
        if self._container is not None:
            raise RuntimeError("Playground deployment is already running")
        self.working_directory.mkdir(parents=True, exist_ok=True)
        self.database_name = f"openmagic_playground_{uuid4().hex}"
        container = PostgresContainer(
            POSTGRES_IMAGE,
            username="openmagic",
            password="openmagic",
            dbname=self.database_name,
            driver=None,
        )
        self._container = container
        try:
            container.start()
            self.database_url = container.get_connection_url(driver=None)
            apply_migrations(self.database_url)
            mark_synthetic_deployment(self.database_url)
            for role, capacity in self.role_capacities.items():
                for _ in range(capacity):
                    self._start_process(role)
            self._refresh()
            for process in self.processes:
                self._wait_ready(process)
            return self.processes
        except BaseException:
            self.stop()
            raise

    def stop(self) -> tuple[ManagedProcess, ...]:
        stopped = tuple(item.public for item in self._running)
        try:
            for running in tuple(reversed(self._running)):
                self._stop_running(running, force=False)
        finally:
            self._running.clear()
            self._refresh()
            try:
                if self._container is not None:
                    self._container.stop()
            finally:
                self._container = None
                self._verification_secret_path.unlink(missing_ok=True)
        return stopped

    def reset(self) -> None:
        """Reset only after every owned process has been explicitly drained."""

        if self._container is None:
            raise RuntimeError("Playground deployment is not running")
        if self._running:
            raise RuntimeError("Playground reset requires all process roles to be drained")
        reset_synthetic_deployment(self.database_url)

    def terminate_role(self, role: ProcessRole) -> ManagedProcess:
        running = next(item for item in self._running if item.public.role == role)
        public = running.public
        self._stop_running(running, force=True)
        self._running.remove(running)
        self._refresh()
        return public

    def restart_role(self, role: ProcessRole) -> ManagedProcess:
        self.drain_role(role)
        return self.scale_role(role, capacity=1)[0]

    def drain_role(self, role: ProcessRole) -> tuple[ManagedProcess, ...]:
        drained: list[ManagedProcess] = []
        for running in tuple(self._running):
            if running.public.role == role:
                drained.append(running.public)
                self._stop_running(running, force=False)
                self._running.remove(running)
        self._refresh()
        return tuple(drained)

    def drain(self) -> tuple[ManagedProcess, ...]:
        drained: list[ManagedProcess] = []
        for role in _SCRIPTS:
            drained.extend(self.drain_role(role))
        return tuple(drained)

    def scale_role(self, role: ProcessRole, *, capacity: int) -> tuple[ManagedProcess, ...]:
        if type(capacity) is not int or capacity < 0:
            raise ValueError("Process-pool capacity must be a non-negative integer")
        current = [item for item in self._running if item.public.role == role]
        for running in reversed(current[capacity:]):
            self._stop_running(running, force=False)
            self._running.remove(running)
        started: list[ManagedProcess] = []
        for _ in range(max(0, capacity - len(current))):
            self._start_process(role)
            started.append(self._running[-1].public)
        self._refresh()
        for process in started:
            self._wait_ready(process)
        return tuple(started)

    def _refresh(self) -> None:
        self.processes = tuple(item.public for item in self._running)

    @staticmethod
    def _stop_running(running: _RunningProcess, *, force: bool) -> None:
        if running.process.poll() is None:
            if force:
                running.process.kill()
            else:
                running.process.terminate()
        try:
            running.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            running.process.kill()
            running.process.wait(timeout=5)
        close = getattr(running.log_handle, "close", None)
        if close is not None:
            close()

    def _start_process(self, role: ProcessRole) -> None:
        port = _free_port()
        script = Path(sys.executable).parent / _SCRIPTS[role]
        if not script.is_file():
            raise RuntimeError(f"installed process entry point is missing: {_SCRIPTS[role]}")
        arguments: list[str] = []
        worker_id: str | None = None
        if role != "api":
            worker_id = f"{role}-{uuid4().hex}"
            arguments.extend(["--worker-id", worker_id])
        if role == "workflow-worker" and self.email_provider_url is not None:
            arguments.extend(["--email-provider-url", self.email_provider_url])
        if role == "workflow-worker":
            descriptor = os.open(
                self._verification_secret_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
                secret_file.write(self.verification_code_secret or _VERIFICATION_SECRET)
            arguments.extend(
                ["--verification-code-secret-file", str(self._verification_secret_path)]
            )
        log_handle = (self.working_directory / f"{role}-{uuid4().hex}.log").open("wb")
        process = subprocess.Popen(
            [
                str(script),
                "--database-url",
                self.database_url,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                *arguments,
            ],
            cwd=self.working_directory,
            env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"},
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
                    worker_id=worker_id,
                ),
                process=process,
                log_handle=log_handle,
            )
        )

    def _wait_ready(self, process: ManagedProcess) -> None:
        deadline = time.monotonic() + self.readiness_timeout
        while time.monotonic() < deadline:
            running = next(item for item in self._running if item.public.pid == process.pid)
            if (exit_code := running.process.poll()) is not None:
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


__all__ = ["ManagedProcess", "PlaygroundDeployment", "ProcessRole"]

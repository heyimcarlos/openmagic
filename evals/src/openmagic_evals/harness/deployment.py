from __future__ import annotations

import json
import os
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

from openmagic_evals.harness._network import free_port
from openmagic_evals.harness._postgres import postgres_container

_SYNTHETIC_VERIFICATION_SECRET = "openmagic-evals-verification-secret"

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


class TestDeployment:
    """Own one PostgreSQL deployment and three installed OS processes."""

    __test__ = False

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
        if set(capacities) != {"api", "workflow-worker", "delivery-worker"}:
            raise ValueError("Process pools require explicit API, Workflow, and Delivery capacity")
        if any(type(capacity) is not int or capacity <= 0 for capacity in capacities.values()):
            raise ValueError("Initial process-pool capacities must be positive integers")
        self.role_capacities = dict(capacities)
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
            scripts: dict[ProcessRole, str] = {
                "api": "openmagic-api",
                "workflow-worker": "openmagic-workflow-worker",
                "delivery-worker": "openmagic-delivery-worker",
            }
            for role, script in scripts.items():
                for _ in range(self.role_capacities[role]):
                    self._start_process(role, script)
            self.processes = tuple(running.public for running in self._running)
            for process in self.processes:
                self._wait_ready(process)
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        for running in tuple(reversed(self._running)):
            self._stop_running(running, force=False)
        self._running.clear()
        self.processes = ()
        if self._container is not None:
            self._container.stop()
            self._container = None

    def terminate_role(self, role: ProcessRole) -> ManagedProcess:
        running = next(item for item in self._running if item.public.role == role)
        public = running.public
        self._stop_running(running, force=True)
        self._running.remove(running)
        self.processes = tuple(item.public for item in self._running)
        return public

    def restart_role(self, role: ProcessRole) -> ManagedProcess:
        running = next((item for item in self._running if item.public.role == role), None)
        if running is not None:
            self._stop_running(running, force=False)
            self._running.remove(running)
        scripts: dict[ProcessRole, str] = {
            "api": "openmagic-api",
            "workflow-worker": "openmagic-workflow-worker",
            "delivery-worker": "openmagic-delivery-worker",
        }
        self._start_process(role, scripts[role])
        public = self._running[-1].public
        self.processes = tuple(item.public for item in self._running)
        self._wait_ready(public)
        return public

    def drain_role(self, role: ProcessRole) -> tuple[ManagedProcess, ...]:
        drained: list[ManagedProcess] = []
        for running in tuple(self._running):
            if running.public.role != role:
                continue
            drained.append(running.public)
            self._stop_running(running, force=False)
            self._running.remove(running)
        self.processes = tuple(item.public for item in self._running)
        return tuple(drained)

    def scale_role(self, role: ProcessRole, *, capacity: int) -> tuple[ManagedProcess, ...]:
        if type(capacity) is not int or capacity < 0:
            raise ValueError("Process-pool capacity must be a non-negative integer")
        current = [item for item in self._running if item.public.role == role]
        if len(current) > capacity:
            for running in reversed(current[capacity:]):
                self._stop_running(running, force=False)
                self._running.remove(running)
        scripts: dict[ProcessRole, str] = {
            "api": "openmagic-api",
            "workflow-worker": "openmagic-workflow-worker",
            "delivery-worker": "openmagic-delivery-worker",
        }
        started: list[ManagedProcess] = []
        for _ in range(max(0, capacity - len(current))):
            self._start_process(role, scripts[role])
            started.append(self._running[-1].public)
        self.processes = tuple(item.public for item in self._running)
        for process in started:
            self._wait_ready(process)
        return tuple(started)

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

    def _start_process(self, role: ProcessRole, script_name: str) -> None:
        port = free_port()
        script = Path(sys.executable).parent / script_name
        if not script.is_file():
            raise RuntimeError(f"installed process entry point is missing: {script_name}")
        worker_arguments = []
        if role != "api":
            worker_arguments = ["--worker-id", f"{role}-{uuid4().hex}"]
        if role == "workflow-worker" and self.email_provider_url is not None:
            worker_arguments.extend(["--email-provider-url", self.email_provider_url])
        if role == "workflow-worker":
            secret_path = self.working_directory / "verification-code-secret"
            descriptor = os.open(
                secret_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
                secret_file.write(self.verification_code_secret or _SYNTHETIC_VERIFICATION_SECRET)
            worker_arguments.extend(["--verification-code-secret-file", str(secret_path)])
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
        log_path = self.working_directory / f"{role}-{uuid4().hex}.log"
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

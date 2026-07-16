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
from ipaddress import ip_address
from pathlib import Path
from types import TracebackType
from typing import Literal
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4

from example_insurance.migrations import apply_migrations
from openmagic_runtime.processes import OwnedProcess, ProcessCleanup
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


def _require_local_provider_url(provider_url: str) -> None:
    parsed = urlsplit(provider_url)
    hostname = parsed.hostname
    if parsed.scheme != "http" or hostname is None or parsed.username is not None:
        raise ValueError("Email provider URL must use HTTP on a local loopback host")
    if hostname == "localhost":
        return
    try:
        local = ip_address(hostname).is_loopback
    except ValueError:
        local = False
    if not local:
        raise ValueError("Email provider URL must use HTTP on a local loopback host")


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
    owner: OwnedProcess


class PlaygroundDeployment:
    """Own one synthetic PostgreSQL deployment and explicit OS processes."""

    def __init__(
        self,
        *,
        working_directory: Path,
        readiness_timeout: float = 30.0,
        shutdown_timeout: float = 5.0,
        email_provider_url: str | None = None,
        verification_code_secret: str | None = None,
        role_capacities: Mapping[ProcessRole, int] | None = None,
    ) -> None:
        self.working_directory = working_directory.resolve()
        self.readiness_timeout = readiness_timeout
        if shutdown_timeout <= 0:
            raise ValueError("Process shutdown timeout must be positive")
        self.shutdown_timeout = shutdown_timeout
        if email_provider_url is not None:
            _require_local_provider_url(email_provider_url)
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
        if self._container is not None or self._running:
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
        except BaseException as start_error:
            try:
                self.stop()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup(
                    "playground start and cleanup failed",
                    [start_error, cleanup_error],
                ) from start_error
            raise

    def stop(self) -> tuple[ManagedProcess, ...]:
        owned = tuple(self._running)
        reaped: list[_RunningProcess] = []
        errors: list[BaseException] = []
        try:
            for running in reversed(owned):
                result = self._stop_running(running, force=False)
                errors.extend(result.errors)
                if result.reaped:
                    reaped.append(running)
        finally:
            self._running[:] = [running for running in owned if running not in reaped]
            self._refresh()
            try:
                if self._container is not None:
                    try:
                        self._container.stop()
                    except Exception as error:
                        errors.append(error)
                    else:
                        self._container = None
            finally:
                try:
                    self._verification_secret_path.unlink(missing_ok=True)
                except Exception as error:
                    errors.append(error)
        if errors:
            raise BaseExceptionGroup("playground cleanup failed", errors)
        return tuple(running.public for running in reaped)

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
        result = self._stop_running(running, force=True)
        if result.reaped:
            self._running.remove(running)
        self._refresh()
        if result.errors:
            raise BaseExceptionGroup(f"failed to terminate playground role {role}", result.errors)
        return public

    def restart_role(self, role: ProcessRole) -> ManagedProcess:
        self.drain_role(role)
        return self.scale_role(role, capacity=1)[0]

    def drain_role(self, role: ProcessRole) -> tuple[ManagedProcess, ...]:
        drained: list[ManagedProcess] = []
        errors: list[BaseException] = []
        for running in tuple(self._running):
            if running.public.role == role:
                result = self._stop_running(running, force=False)
                errors.extend(result.errors)
                if result.reaped:
                    self._running.remove(running)
                    drained.append(running.public)
        self._refresh()
        if errors:
            raise BaseExceptionGroup(f"failed to drain playground role {role}", errors)
        return tuple(drained)

    def drain(self) -> tuple[ManagedProcess, ...]:
        drained: list[ManagedProcess] = []
        errors: list[BaseException] = []
        for role in _SCRIPTS:
            try:
                drained.extend(self.drain_role(role))
            except BaseException as error:
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("failed to drain playground deployment", errors)
        return tuple(drained)

    def scale_role(self, role: ProcessRole, *, capacity: int) -> tuple[ManagedProcess, ...]:
        if type(capacity) is not int or capacity < 0:
            raise ValueError("Process-pool capacity must be a non-negative integer")
        current = [item for item in self._running if item.public.role == role]
        errors: list[BaseException] = []
        for running in reversed(current[capacity:]):
            result = self._stop_running(running, force=False)
            errors.extend(result.errors)
            if result.reaped:
                self._running.remove(running)
        if errors:
            self._refresh()
            raise BaseExceptionGroup(f"failed to scale playground role {role}", errors)
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

    def _stop_running(
        self,
        running: _RunningProcess,
        *,
        force: bool,
    ) -> ProcessCleanup:
        return running.owner.reap(
            timeout_seconds=self.shutdown_timeout,
            forced_loss=force,
        )

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
        try:
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
        except BaseException:
            log_handle.close()
            raise
        running = _RunningProcess(
            public=ManagedProcess(
                role=role,
                pid=process.pid,
                health_url=f"http://127.0.0.1:{port}/health",
                worker_id=worker_id,
            ),
            process=process,
            owner=OwnedProcess.subprocess(process, resources=(log_handle,)),
        )
        try:
            self._running.append(running)
        except BaseException as ownership_error:
            result = self._stop_running(running, force=False)
            if result.errors:
                raise BaseExceptionGroup(
                    "playground process ownership and cleanup failed",
                    [ownership_error, *result.errors],
                ) from ownership_error
            raise

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

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from io import BufferedWriter
from pathlib import Path
from types import TracebackType
from urllib.error import URLError
from urllib.request import Request, urlopen

from openmagic_evals.harness._network import free_port


class LocalEmailProvider:
    def __init__(
        self,
        *,
        working_directory: Path,
        readiness_timeout: float = 10.0,
        shutdown_timeout: float = 5.0,
    ) -> None:
        if readiness_timeout <= 0 or shutdown_timeout <= 0:
            raise ValueError("Provider process timeouts must be positive")
        self.working_directory = working_directory.resolve()
        self.readiness_timeout = readiness_timeout
        self.shutdown_timeout = shutdown_timeout
        self.state_path = self.working_directory / "email-provider.sqlite3"
        self._port = free_port()
        self.url = f"http://127.0.0.1:{self._port}"
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: BufferedWriter | None = None

    def __enter__(self) -> LocalEmailProvider:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    @property
    def pid(self) -> int:
        if self._process is None:
            raise RuntimeError("Local email provider is not running")
        return self._process.pid

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("Local email provider is already running")
        self.working_directory.mkdir(parents=True, exist_ok=True)
        script = Path(sys.executable).parent / "openmagic-local-email-provider"
        if not script.is_file():
            raise RuntimeError("installed local email provider entry point is missing")
        self._log_handle = (self.working_directory / "email-provider.log").open("ab")
        try:
            self._process = subprocess.Popen(
                [
                    str(script),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self._port),
                    "--state-path",
                    str(self.state_path),
                ],
                cwd=self.working_directory,
                env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"},
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + self.readiness_timeout
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    raise RuntimeError("Local email provider exited before readiness")
                try:
                    with urlopen(f"{self.url}/health", timeout=0.5) as response:
                        if json.load(response) == {"status": "ready"}:
                            return
                except (OSError, URLError, ValueError):
                    time.sleep(0.02)
            raise TimeoutError("Local email provider did not become ready")
        except BaseException as start_error:
            try:
                self._reap()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup(
                    "local email provider start and cleanup failed",
                    [start_error, cleanup_error],
                ) from start_error
            raise

    def stop(self) -> None:
        self._reap()

    def _reap(self) -> None:
        errors: list[Exception] = []
        process = self._process
        exited = process is None
        try:
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
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
                errors.append(RuntimeError(f"local email provider {process.pid} survived cleanup"))
            exited = process is None or process.poll() is not None
        finally:
            if exited:
                self._process = None
            if exited and self._log_handle is not None:
                try:
                    self._log_handle.close()
                except Exception as error:
                    errors.append(error)
                finally:
                    self._log_handle = None
        if errors:
            raise ExceptionGroup("local email provider cleanup failed", errors)

    def restart(self) -> None:
        self.stop()
        self.start()

    def configure(
        self,
        *,
        behaviors: tuple[str, ...],
        reconciliation: str = "unchanged",
        delay_seconds: float = 0,
    ) -> None:
        request = Request(
            f"{self.url}/configure",
            data=json.dumps(
                {
                    "behaviors": list(behaviors),
                    "reconciliation": reconciliation,
                    "delay_seconds": delay_seconds,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            if json.load(response) != {"status": "configured"}:
                raise RuntimeError("Local email provider rejected configuration")

    def requests(self) -> tuple[dict[str, object], ...]:
        with urlopen(f"{self.url}/requests", timeout=2) as response:
            value = json.load(response)
        return tuple(value["requests"])

    def request_count(self) -> int:
        with urlopen(f"{self.url}/request-count", timeout=2) as response:
            value = json.load(response)
        return int(value["request_count"])

    def reconciliations(self) -> tuple[dict[str, object], ...]:
        with urlopen(f"{self.url}/reconciliations", timeout=2) as response:
            value = json.load(response)
        return tuple(value["reconciliations"])


__all__ = ["LocalEmailProvider"]

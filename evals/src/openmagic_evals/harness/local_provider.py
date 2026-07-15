from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import TracebackType
from urllib.error import URLError
from urllib.request import Request, urlopen

from openmagic_evals.harness._network import free_port


class LocalEmailProvider:
    def __init__(self, *, working_directory: Path) -> None:
        self.working_directory = working_directory.resolve()
        self.state_path = self.working_directory / "email-provider.sqlite3"
        self._port = free_port()
        self.url = f"http://127.0.0.1:{self._port}"
        self._process: subprocess.Popen[bytes] | None = None

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
        log = (self.working_directory / "email-provider.log").open("ab")
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
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + 10
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

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        self._process = None

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

    def reconciliations(self) -> tuple[dict[str, object], ...]:
        with urlopen(f"{self.url}/reconciliations", timeout=2) as response:
            value = json.load(response)
        return tuple(value["reconciliations"])


__all__ = ["LocalEmailProvider"]

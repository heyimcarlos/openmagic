from __future__ import annotations

import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from openmagic_evals.evidence.live_smoke import provider_configuration_digest, run_live_smoke


class _ResponsesHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        assert payload["store"] is False
        assert payload["metadata"] == {"openmagic_case_id": "live.synthetic.test"}
        assert "OPENMAGIC_SYNTHETIC_SMOKE_OK" in payload["input"]
        document = json.dumps({"output_text": "OPENMAGIC_SYNTHETIC_SMOKE_OK"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(document)))
        self.send_header("x-request-id", "synthetic-request-1")
        self.end_headers()
        self.wfile.write(document)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def _clean_repository(path: Path) -> None:
    path.mkdir()
    (path / "uv.lock").write_text("synthetic lock\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "uv.lock"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "test fixture"], cwd=path, check=True)


def test_live_smoke_posts_and_verifies_one_reversible_synthetic_case(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _clean_repository(repository)
    credential = tmp_path / "credential"
    credential.write_text("synthetic-credential", encoding="utf-8")
    credential.chmod(0o600)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ResponsesHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        artifact = run_live_smoke(
            repository_root=repository,
            output=tmp_path / "live-smoke.json",
            provider="openai-responses",
            model="synthetic-model",
            endpoint=f"http://127.0.0.1:{server.server_port}/v1/responses",
            configuration_digest=provider_configuration_digest(
                provider="openai-responses",
                model="synthetic-model",
                endpoint=f"http://127.0.0.1:{server.server_port}/v1/responses",
            ),
            synthetic_case_id="live.synthetic.test",
            credential_file=credential,
            allow_live=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert artifact.summary.attempted
    assert artifact.summary.available
    assert artifact.cases[0].correlations.provider_request_ids == ("synthetic-request-1",)


def test_live_smoke_rejects_unpinned_configuration_and_arbitrary_credential_hosts(
    tmp_path: Path,
) -> None:
    credential = tmp_path / "credential"
    credential.write_text("synthetic-credential", encoding="utf-8")
    credential.chmod(0o600)
    with pytest.raises(ValueError, match="configuration digest"):
        run_live_smoke(
            repository_root=tmp_path,
            output=tmp_path / "live.json",
            provider="openai-responses",
            model="synthetic-model",
            endpoint="https://api.openai.com/v1/responses",
            configuration_digest="sha256:" + "0" * 64,
            synthetic_case_id="live.synthetic.test",
            credential_file=credential,
            allow_live=True,
        )
    with pytest.raises(ValueError, match="endpoint"):
        run_live_smoke(
            repository_root=tmp_path,
            output=tmp_path / "live.json",
            provider="openai-responses",
            model="synthetic-model",
            endpoint="http://attacker.example/v1/responses",
            configuration_digest=None,
            synthetic_case_id="live.synthetic.test",
            credential_file=credential,
            allow_live=True,
        )

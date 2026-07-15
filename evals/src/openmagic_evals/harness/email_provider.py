"""Independent deterministic HTTP email provider used by release-gate evals."""

from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS configuration ("
        "singleton integer PRIMARY KEY CHECK (singleton = 1), "
        "behaviors text NOT NULL, reconciliation text NOT NULL, delay_seconds real NOT NULL)"
    )
    connection.execute(
        "INSERT OR IGNORE INTO configuration VALUES (1, '[\"success\"]', 'unchanged', 0)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS effects ("
        "logical_effect_id text PRIMARY KEY, provider_request_id text NOT NULL, "
        "classification text NOT NULL, idempotency_key text NOT NULL UNIQUE)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS requests ("
        "sequence integer PRIMARY KEY AUTOINCREMENT, logical_effect_id text NOT NULL, "
        "provider_request_id text NOT NULL, idempotency_key text NOT NULL, "
        "recipient_email text NOT NULL, subject text NOT NULL, body text NOT NULL, "
        "duplicate integer NOT NULL, behavior text NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS reconciliations ("
        "sequence integer PRIMARY KEY AUTOINCREMENT, logical_effect_id text NOT NULL, "
        "behavior text NOT NULL)"
    )
    connection.commit()
    return connection


class EmailProviderHandler(BaseHTTPRequestHandler):
    server: EmailProviderServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/health":
            self._respond(200, {"status": "ready"})
            return
        if self.path == "/requests":
            with _connect(self.server.state_path) as connection:
                rows = connection.execute(
                    "SELECT sequence, logical_effect_id, provider_request_id, idempotency_key, "
                    "recipient_email, subject, body, duplicate, behavior FROM requests "
                    "ORDER BY sequence"
                ).fetchall()
            self._respond(200, {"requests": [dict(row) for row in rows]})
            return
        if self.path == "/reconciliations":
            with _connect(self.server.state_path) as connection:
                rows = connection.execute(
                    "SELECT sequence, logical_effect_id, behavior FROM reconciliations "
                    "ORDER BY sequence"
                ).fetchall()
            self._respond(200, {"reconciliations": [dict(row) for row in rows]})
            return
        if self.path.startswith("/effects/"):
            self._reconcile(self.path.removeprefix("/effects/"))
            return
        self._respond(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/configure":
            value = self._request_json()
            behaviors = value.get("behaviors", ["success"])
            reconciliation = value.get("reconciliation", "unchanged")
            delay_seconds = value.get("delay_seconds", 0)
            allowed = {
                "success",
                "definite_not_applied",
                "uncertain",
                "response_loss_after_success",
                "slow_success",
            }
            if (
                not isinstance(behaviors, list)
                or not behaviors
                or any(item not in allowed for item in behaviors)
                or reconciliation
                not in {
                    "unchanged",
                    "applied",
                    "not_applied",
                    "uncertain",
                    "slow_applied",
                }
                or not isinstance(delay_seconds, (int, float))
                or delay_seconds < 0
            ):
                self._respond(400, {"error": "invalid_configuration"})
                return
            with _connect(self.server.state_path) as connection:
                connection.execute(
                    "UPDATE configuration SET behaviors = ?, reconciliation = ?, "
                    "delay_seconds = ? WHERE singleton = 1",
                    (json.dumps(behaviors), reconciliation, float(delay_seconds)),
                )
                connection.commit()
            self._respond(200, {"status": "configured"})
            return
        if self.path == "/dispatch":
            self._dispatch(self._request_json())
            return
        self._respond(404, {"error": "not_found"})

    def _dispatch(self, value: dict[str, Any]) -> None:
        required = {
            "logical_effect_id",
            "idempotency_key",
            "recipient_email",
            "subject",
            "body",
        }
        if set(value) != required or any(not isinstance(value[key], str) for key in required):
            self._respond(400, {"error": "invalid_dispatch"})
            return
        with _connect(self.server.state_path) as connection:
            existing = connection.execute(
                "SELECT provider_request_id, classification FROM effects WHERE idempotency_key = ?",
                (value["idempotency_key"],),
            ).fetchone()
            config = connection.execute(
                "SELECT behaviors, delay_seconds FROM configuration WHERE singleton = 1"
            ).fetchone()
            if config is None:
                raise RuntimeError("Provider configuration disappeared")
            behaviors = json.loads(str(config[0]))
            behavior = str(behaviors[0])
            if len(behaviors) > 1:
                connection.execute(
                    "UPDATE configuration SET behaviors = ? WHERE singleton = 1",
                    (json.dumps(behaviors[1:]),),
                )
            request_id = str(existing[0]) if existing is not None else str(uuid4())
            connection.execute(
                "INSERT INTO requests "
                "(logical_effect_id, provider_request_id, idempotency_key, recipient_email, "
                "subject, body, duplicate, behavior) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    value["logical_effect_id"],
                    request_id,
                    value["idempotency_key"],
                    value["recipient_email"],
                    value["subject"],
                    value["body"],
                    int(existing is not None),
                    "duplicate" if existing is not None else behavior,
                ),
            )
            connection.commit()
            if existing is not None:
                self._respond(
                    200,
                    {
                        "classification": str(existing[1]),
                        "provider_request_id": request_id,
                        "duplicate": True,
                    },
                )
                return
            if behavior == "slow_success":
                time.sleep(float(config[1]))
                behavior = "success"
            if behavior == "definite_not_applied":
                self._respond(
                    422,
                    {
                        "classification": "not_applied",
                        "provider_request_id": request_id,
                        "duplicate": False,
                    },
                )
                return
            classification = {
                "success": "applied",
                "response_loss_after_success": "applied",
                "uncertain": "uncertain",
            }[behavior]
            connection.execute(
                "INSERT INTO effects "
                "(logical_effect_id, provider_request_id, classification, idempotency_key) "
                "VALUES (?, ?, ?, ?)",
                (
                    value["logical_effect_id"],
                    request_id,
                    classification,
                    value["idempotency_key"],
                ),
            )
            connection.commit()
        if behavior == "response_loss_after_success":
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        status = 200 if classification == "applied" else 202
        self._respond(
            status,
            {
                "classification": classification,
                "provider_request_id": request_id,
                "duplicate": False,
            },
        )

    def _reconcile(self, logical_effect_id: str) -> None:
        with _connect(self.server.state_path) as connection:
            effect = connection.execute(
                "SELECT provider_request_id, classification FROM effects "
                "WHERE logical_effect_id = ?",
                (logical_effect_id,),
            ).fetchone()
            config = connection.execute(
                "SELECT reconciliation, delay_seconds FROM configuration WHERE singleton = 1"
            ).fetchone()
            if config is None:
                raise RuntimeError("Provider configuration disappeared")
            reconciliation = str(config[0])
            connection.execute(
                "INSERT INTO reconciliations (logical_effect_id, behavior) VALUES (?, ?)",
                (logical_effect_id, reconciliation),
            )
            connection.commit()
            if effect is None:
                self._respond(404, {"error": "not_found"})
                return
            if reconciliation == "slow_applied":
                time.sleep(float(config[1]))
                reconciliation = "applied"
            classification = str(effect[1]) if reconciliation == "unchanged" else reconciliation
            if classification != str(effect[1]):
                connection.execute(
                    "UPDATE effects SET classification = ? WHERE logical_effect_id = ?",
                    (classification, logical_effect_id),
                )
                connection.commit()
        self._respond(
            200,
            {
                "classification": classification,
                "provider_request_id": str(effect[0]),
            },
        )

    def _request_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Provider request body must be an object")
        return value

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class EmailProviderServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], state_path: Path) -> None:
        self.state_path = state_path
        _connect(state_path).close()
        super().__init__(address, EmailProviderHandler)


def main() -> None:
    parser = argparse.ArgumentParser(prog="openmagic-local-email-provider")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--state-path", required=True, type=Path)
    arguments = parser.parse_args()
    EmailProviderServer(
        (arguments.host, arguments.port), arguments.state_path.resolve()
    ).serve_forever()


if __name__ == "__main__":
    main()

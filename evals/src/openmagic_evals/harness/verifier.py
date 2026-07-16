from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import urlopen

import psycopg
from openmagic_playground import PlaygroundDeployment


@dataclass(frozen=True)
class BootVerdict:
    passed: bool
    violations: tuple[str, ...]


class DeploymentVerifier:
    """Judge process and schema evidence independently of process internals."""

    def __init__(self, deployment: PlaygroundDeployment) -> None:
        self.deployment = deployment

    def verify_boot(
        self,
        *,
        required_schemas: tuple[str, ...] = ("openmagic_runtime", "example_insurance"),
    ) -> BootVerdict:
        violations: list[str] = []
        expected_roles = {"api", "workflow-worker", "delivery-worker"}
        actual_roles = {process.role for process in self.deployment.processes}
        if actual_roles != expected_roles:
            violations.append("installed process roles do not match the deployment contract")
        pids = [process.pid for process in self.deployment.processes]
        if len(pids) != len(set(pids)):
            violations.append("process roles do not have distinct operating-system identities")

        for process in self.deployment.processes:
            try:
                with urlopen(process.health_url, timeout=2) as response:
                    payload = json.load(response)
            except (OSError, URLError, ValueError) as error:
                violations.append(f"{process.role} health interface failed: {type(error).__name__}")
                continue
            if payload.get("role") != process.role:
                violations.append(f"{process.role} reported the wrong role")
            if payload.get("pid") != process.pid:
                violations.append(f"{process.role} reported the wrong process identity")
            if payload.get("database") != self.deployment.database_name:
                violations.append(f"{process.role} reported the wrong database")

        with (
            psycopg.connect(self.deployment.database_url) as connection,
            connection.transaction(),
        ):
            connection.execute("SET TRANSACTION READ ONLY")
            schemas = {
                str(row[0])
                for row in connection.execute(
                    "SELECT schema_name FROM information_schema.schemata"
                ).fetchall()
            }
        for schema in required_schemas:
            if schema not in schemas:
                violations.append(f"required schema is missing: {schema}")
        return BootVerdict(passed=not violations, violations=tuple(violations))


__all__ = ["BootVerdict", "DeploymentVerifier"]

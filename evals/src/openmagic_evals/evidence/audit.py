"""Automated public-surface and cold-schema audits."""

from __future__ import annotations

import ast
import tomllib
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

_RUNTIME_PUBLIC_MODULES = (
    "agents.py",
    "commands.py",
    "delivery.py",
    "evidence.py",
    "execution.py",
    "kernel/control.py",
    "kernel/definitions.py",
    "kernel/inspection.py",
    "kernel/records.py",
    "kernel/work.py",
    "threads.py",
    "workers.py",
)
_FORBIDDEN_EXPORTS = {"Connection", "MigrationBundle", "Model", "Repository", "Row", "Session"}
_FORBIDDEN_PRODUCT_IMPORTS = {
    "openmagic_evals",
    "openmagic_playground",
}
_DELETED_IDENTIFIERS = (
    "server" + ".workflows",
    "workflow" + "_jobs",
    "workflow" + "_job_runs",
    "workflow" + "_events",
    "notifi" + "cations",
    "Workflow" + "Job",
    "Interaction" + "Agent",
)
_EXPECTED_TABLES = {
    "example_insurance": {
        "approval_grants",
        "deployment_metadata",
        "domain_events",
        "external_effect_evidence",
        "external_effects",
        "migration_history",
        "organization_memberships",
        "parties",
        "party_identifiers",
        "policy_renewal_facts",
        "protected_commands",
        "renewal_decisions",
        "renewal_drafts",
        "renewal_workflows",
        "verification_challenges",
        "verification_events",
        "verification_sessions",
        "verification_workflows",
        "workflow_participants",
        "workflow_role_assignments",
    },
    "openmagic_runtime": {
        "agent_runs",
        "attempts",
        "command_receipts",
        "deliveries",
        "delivery_attempts",
        "deployment_metadata",
        "instances",
        "messages",
        "migration_history",
        "signals",
        "step_dependencies",
        "steps",
        "threads",
        "trace_events",
        "waits",
        "workflow_definitions",
    },
}


@dataclass(frozen=True)
class RepositoryAudit:
    passed: bool
    violations: tuple[str, ...]
    audited_distributions: tuple[str, ...]
    production_dependency_edges: tuple[str, ...]
    private_persistence_packages: tuple[str, ...]


@dataclass(frozen=True)
class ColdSchemaAudit:
    passed: bool
    violations: tuple[str, ...]
    schemas: tuple[str, ...]
    tables: dict[str, tuple[str, ...]]
    migration_heads: dict[str, str]
    legacy_relations: tuple[str, ...]


def _imports(source_root: Path) -> set[str]:
    imported: set[str] = set()
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    return imported


def _dependencies(project: Path) -> set[str]:
    document = tomllib.loads(project.read_text(encoding="utf-8"))
    return {
        dependency.split("[", 1)[0].split("=", 1)[0].split("<", 1)[0].strip()
        for dependency in document["project"].get("dependencies", [])
    }


def _declared_exports(path: Path) -> set[str] | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        value = node.value
        if isinstance(value, (ast.List, ast.Tuple)):
            return {
                element.value
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    return None


def audit_repository(root: Path) -> RepositoryAudit:
    root = root.resolve()
    violations: list[str] = []
    runtime_root = root / "packages/openmagic-runtime/src/openmagic_runtime"
    application_root = root / "reference-apps/example-insurance/src/example_insurance"
    api_root = root / "apps/api/src/openmagic_api"
    production_roots = (runtime_root, application_root, api_root)

    runtime_imports = _imports(runtime_root)
    application_imports = _imports(application_root)
    api_imports = _imports(api_root)
    if any(
        name.startswith(("example_insurance", "openmagic_api", "openmagic_evals"))
        for name in runtime_imports
    ):
        violations.append("runtime imports an outward distribution")
    if any(name.startswith(("openmagic_api", "openmagic_evals")) for name in application_imports):
        violations.append("application imports an outward distribution")
    if any(name.startswith(tuple(_FORBIDDEN_PRODUCT_IMPORTS)) for name in api_imports):
        violations.append("API imports a private evidence or demonstration distribution")

    projects = {
        "openmagic-runtime": root / "packages/openmagic-runtime/pyproject.toml",
        "example-insurance": root / "reference-apps/example-insurance/pyproject.toml",
        "openmagic-api": root / "apps/api/pyproject.toml",
        "openmagic-evals": root / "evals/pyproject.toml",
    }
    internal_names = set(projects)
    production_edges = tuple(
        sorted(
            f"{owner} -> {dependency}"
            for owner in ("openmagic-runtime", "example-insurance", "openmagic-api")
            for dependency in _dependencies(projects[owner]) & internal_names
        )
    )
    expected_edges = (
        "example-insurance -> openmagic-runtime",
        "openmagic-api -> example-insurance",
        "openmagic-api -> openmagic-runtime",
    )
    if production_edges != expected_edges:
        violations.append("production dependency graph differs from the accepted one-way graph")

    root_exports = _declared_exports(runtime_root / "__init__.py")
    if root_exports != {"__version__"}:
        violations.append("runtime root exports more than package metadata")
    for relative in _RUNTIME_PUBLIC_MODULES:
        exports = _declared_exports(runtime_root / relative)
        if exports is None:
            violations.append(f"runtime public module has no explicit exports: {relative}")
        elif exports & _FORBIDDEN_EXPORTS:
            violations.append(f"runtime public module exports persistence details: {relative}")

    scan_roots = (*production_roots, root / "evals")
    for source_root in scan_roots:
        for path in source_root.rglob("*"):
            if path.suffix not in {".py", ".sql"} or "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            for identifier in _DELETED_IDENTIFIERS:
                if identifier in source:
                    violations.append(
                        f"deleted compatibility identifier remains: {path.relative_to(root)}"
                    )

    for path in application_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "openmagic_runtime." in node.value
            ):
                violations.append(
                    f"application SQL references private runtime persistence: {path.name}:{node.lineno}"
                )

    if any((root / "server").rglob("*.py")) or (root / "alembic.ini").exists():
        violations.append("deleted source or migration ownership remains")

    return RepositoryAudit(
        passed=not violations,
        violations=tuple(sorted(set(violations))),
        audited_distributions=tuple(sorted(projects)),
        production_dependency_edges=production_edges,
        private_persistence_packages=(
            "example_insurance._persistence",
            "openmagic_runtime._persistence",
        ),
    )


def audit_cold_schema(database_url: str) -> ColdSchemaAudit:
    violations: list[str] = []
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        schema_rows = connection.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s) ORDER BY nspname",
            (list(_EXPECTED_TABLES),),
        ).fetchall()
        schemas = tuple(str(row[0]) for row in schema_rows)
        table_rows = connection.execute(
            "SELECT schemaname, tablename FROM pg_catalog.pg_tables "
            "WHERE schemaname = ANY(%s) ORDER BY schemaname, tablename",
            (list(_EXPECTED_TABLES),),
        ).fetchall()
        tables = {
            schema: tuple(str(row[1]) for row in table_rows if row[0] == schema)
            for schema in schemas
        }
        heads: dict[str, str] = {}
        for schema in schemas:
            row = connection.execute(
                sql.SQL(
                    "SELECT version FROM {}.migration_history ORDER BY version DESC LIMIT 1"
                ).format(sql.Identifier(schema))
            ).fetchone()
            if row is not None:
                heads[schema] = str(row[0])

    if set(schemas) != set(_EXPECTED_TABLES):
        violations.append("cold database does not contain exactly the owned schemas")
    for schema, expected in _EXPECTED_TABLES.items():
        actual = set(tables.get(schema, ()))
        if actual != expected:
            violations.append(f"cold schema table set differs from baseline: {schema}")
    legacy = tuple(
        sorted(
            f"{schema}.{table}"
            for schema, names in tables.items()
            for table in names
            if table in _DELETED_IDENTIFIERS
        )
    )
    if legacy:
        violations.append("cold database contains a deleted relation")
    return ColdSchemaAudit(
        passed=not violations,
        violations=tuple(violations),
        schemas=schemas,
        tables=tables,
        migration_heads=heads,
        legacy_relations=legacy,
    )


__all__ = [
    "ColdSchemaAudit",
    "RepositoryAudit",
    "audit_cold_schema",
    "audit_repository",
]

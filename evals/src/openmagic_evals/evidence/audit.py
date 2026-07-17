"""Automated public-surface and cold-schema audits."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

from openmagic_evals.evidence.core_models import canonical_digest
from openmagic_evals.evidence.package_policy import (
    PACKAGE_ROLES,
    project_dependencies,
    python_imports,
    role_dependency_violations,
    role_import_violations,
    role_private_import_violations,
    role_public_persistence_violations,
    role_sql_ownership_violations,
    source_python_files,
)
from openmagic_evals.evidence.surface_contracts import (
    APPLICATION_PUBLIC_EXPORTS,
    DELETED_IDENTIFIERS,
    EXPECTED_PRODUCTION_EDGES,
    PUBLIC_SURFACE_DIGESTS,
    RUNTIME_PUBLIC_EXPORTS,
)

_EXPECTED_TABLES = {
    "public": set(),
    "example_insurance": {
        "approval_grants",
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
_EXPECTED_MIGRATIONS = {
    "example_insurance": (
        "0001_example_insurance_baseline",
        "0002_renewal_drafting_application",
        "0003_renewal_approval_effect",
        "0004_deterministic_verification",
    ),
    "openmagic_runtime": (
        "0001_runtime_baseline",
        "0002_renewal_drafting_runtime",
        "0003_fenced_effect_kernel",
    ),
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


def _public_exports(source_root: Path) -> dict[str, tuple[str, ...]]:
    modules = (
        path
        for path in source_root.rglob("*.py")
        if all(not part.startswith("_") for part in path.relative_to(source_root).parent.parts)
        and (path.name == "__init__.py" or not path.name.startswith("_"))
    )
    result: dict[str, tuple[str, ...]] = {}
    for path in modules:
        exports = _declared_exports(path)
        if exports is None:
            raise ValueError(f"public module has no explicit exports: {path}")
        result[path.relative_to(source_root).as_posix()] = tuple(sorted(exports))
    return dict(sorted(result.items()))


def audit_repository(root: Path) -> RepositoryAudit:
    root = root.resolve()
    violations: list[str] = []
    runtime_root = root / "packages/openmagic-runtime/src/openmagic_runtime"
    application_root = root / "reference-apps/example-insurance/src/example_insurance"
    projects = {role.distribution: root / role.project for role in PACKAGE_ROLES}
    for role in PACKAGE_ROLES:
        imports = python_imports(source_python_files(root / role.source))
        dependencies = project_dependencies(root / role.project)
        violations.extend(role_import_violations(role, imports))
        violations.extend(role_private_import_violations(role, imports))
        violations.extend(
            role_public_persistence_violations(
                role,
                source_python_files(root / role.source),
            )
        )
        violations.extend(
            role_sql_ownership_violations(
                role,
                source_python_files(root / role.source),
            )
        )
        violations.extend(role_dependency_violations(role, dependencies))
        exports = _public_exports(root / role.source)
        if canonical_digest(exports) != PUBLIC_SURFACE_DIGESTS[role.distribution]:
            violations.append(
                f"{role.distribution} public modules or exports differ from the exact surface"
            )

    production_names = {"openmagic-runtime", "example-insurance", "openmagic-api"}
    production_edges = tuple(
        sorted(
            f"{owner} -> {dependency}"
            for owner in production_names
            for dependency in project_dependencies(projects[owner]) & production_names
        )
    )
    if production_edges != EXPECTED_PRODUCTION_EDGES:
        violations.append("production dependency graph differs from the accepted one-way graph")

    if _public_exports(runtime_root) != RUNTIME_PUBLIC_EXPORTS:
        violations.append("runtime modules or exports differ from the exact accepted surface")
    if _public_exports(application_root) != APPLICATION_PUBLIC_EXPORTS:
        violations.append("application modules or exports differ from the exact accepted surface")

    scan_roots = (
        root / "packages/openmagic-runtime",
        root / "reference-apps/example-insurance",
        root / "apps/api",
        root / "apps/playground",
        root / "evals",
    )
    for source_root in scan_roots:
        for path in source_root.rglob("*"):
            if path.suffix not in {".py", ".sql", ".toml"} or "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            for identifier in DELETED_IDENTIFIERS:
                if identifier in source:
                    violations.append(
                        f"deleted compatibility identifier remains: {path.relative_to(root)}"
                    )

    for path in application_root.rglob("*.py"):
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
            "openmagic_runtime.kernel._persistence",
        ),
    )


def audit_cold_schema(database_url: str) -> ColdSchemaAudit:
    violations: list[str] = []
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        schema_rows = connection.execute(
            "SELECT nspname FROM pg_namespace "
            "WHERE nspname <> 'information_schema' AND nspname NOT LIKE 'pg_%' "
            "ORDER BY nspname"
        ).fetchall()
        schemas = tuple(str(row[0]) for row in schema_rows)
        table_rows = connection.execute(
            "SELECT schemaname, tablename FROM pg_catalog.pg_tables "
            "WHERE schemaname <> 'information_schema' AND schemaname NOT LIKE 'pg_%' "
            "ORDER BY schemaname, tablename"
        ).fetchall()
        tables = {
            schema: tuple(str(row[1]) for row in table_rows if row[0] == schema)
            for schema in schemas
        }
        heads: dict[str, str] = {}
        histories: dict[str, tuple[str, ...]] = {}
        for schema in ("example_insurance", "openmagic_runtime"):
            rows = connection.execute(
                sql.SQL("SELECT version FROM {}.migration_history ORDER BY version").format(
                    sql.Identifier(schema)
                )
            ).fetchall()
            histories[schema] = tuple(str(row[0]) for row in rows)
            if rows:
                heads[schema] = str(rows[-1][0])

    if set(schemas) != set(_EXPECTED_TABLES):
        violations.append("cold database does not contain exactly the owned schemas")
    for schema, expected in _EXPECTED_TABLES.items():
        actual = set(tables.get(schema, ()))
        if actual != expected:
            violations.append(f"cold schema table set differs from baseline: {schema}")
    for schema, expected in _EXPECTED_MIGRATIONS.items():
        if histories.get(schema) != expected:
            violations.append(f"cold schema migration history differs from baseline: {schema}")
    legacy = tuple(
        sorted(
            f"{schema}.{table}"
            for schema, names in tables.items()
            for table in names
            if table in DELETED_IDENTIFIERS
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

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import example_insurance
import openmagic_api
import openmagic_evals
import openmagic_playground
import openmagic_runtime
import pytest
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

ROOT = Path(__file__).parents[2]

RUNTIME_PUBLIC_MODULES = (
    "openmagic_runtime.kernel.definitions",
    "openmagic_runtime.kernel.control",
    "openmagic_runtime.kernel.work",
    "openmagic_runtime.kernel.inspection",
    "openmagic_runtime.commands",
    "openmagic_runtime.execution",
    "openmagic_runtime.processes",
    "openmagic_runtime.agents",
    "openmagic_runtime.threads",
    "openmagic_runtime.delivery",
    "openmagic_runtime.workers",
    "openmagic_runtime.evidence",
)


def _from_imports(path: Path) -> set[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
        for alias in node.names
    }


def test_production_dependency_direction_is_one_way() -> None:
    for role in PACKAGE_ROLES:
        imports = python_imports(source_python_files(ROOT / role.source))
        dependencies = project_dependencies(ROOT / role.project)
        assert role_import_violations(role, imports) == ()
        assert role_private_import_violations(role, imports) == ()
        assert role_dependency_violations(role, dependencies) == ()
        assert (
            role_public_persistence_violations(role, source_python_files(ROOT / role.source)) == ()
        )
        assert role_sql_ownership_violations(role, source_python_files(ROOT / role.source)) == ()


def test_cross_distribution_private_import_policy_rejects_full_paths(tmp_path: Path) -> None:
    fixture = tmp_path / "consumer.py"
    fixture.write_text(
        "import openmagic_runtime._persistence\n"
        "import openmagic_runtime._canonical\n"
        "from example_insurance import _persistence, _internal\n",
        encoding="utf-8",
    )
    imports = python_imports((fixture,))
    api_role = next(role for role in PACKAGE_ROLES if role.distribution == "openmagic-api")

    assert "openmagic_runtime._persistence" in imports
    assert "openmagic_runtime._canonical" in imports
    assert "example_insurance._persistence" in imports
    assert "example_insurance._internal" in imports
    assert role_private_import_violations(api_role, imports) == (
        "openmagic-api imports private package example_insurance._internal",
        "openmagic-api imports private package example_insurance._persistence",
        "openmagic-api imports private package openmagic_runtime._canonical",
        "openmagic-api imports private package openmagic_runtime._persistence",
    )


def test_public_persistence_policy_rejects_record_adapter_module(tmp_path: Path) -> None:
    package = tmp_path / "example_insurance"
    package.mkdir()
    adapter = package / "leaked_records.py"
    adapter.write_text("__all__ = []\n", encoding="utf-8")
    role = next(role for role in PACKAGE_ROLES if role.distribution == "example-insurance")

    assert role_public_persistence_violations(role, (adapter,)) == (
        "example-insurance exposes persistence adapter leaked_records.py",
    )


@pytest.mark.parametrize(
    "source",
    [
        'connection.execute("SET TRANSACTION READ ONLY")',
        'connection.execute("WITH value AS (SELECT 1) SELECT * FROM value")',
        'connection.execute("MERGE INTO target USING source ON false")',
        'connection.execute("TRUNCATE TABLE target")',
        'connection.execute("LOCK TABLE target")',
        'connection.execute("CALL refresh_target()")',
        "connection.execute(statement)",
        "connection.cursor()",
        "sql.SQL(template)",
        'db.execute("SELECT 1")',
        'tx.execute("SELECT 1")',
        'session.execute("SELECT 1")',
        'self.db.execute("SELECT 1")',
        'factory().connection().execute("SELECT 1")',
        'statement = "SELECT 1"; db.execute(statement)',
        "statement = sql.SQL(template); self.session.execute(statement)",
        'q = "SELECT 1"; db.execute(q)',
        'run_sql = db.execute; run_sql("SELECT 1")',
        'q = "SELECT 1"; run_sql = self.db.execute; run_sql(q)',
        'db.executemany("INSERT INTO target VALUES (%s)", rows)',
        'q = f"SELECT {column} FROM target"; factory().connection().execute(q)',
        "payload = make_statement(); db.execute(payload)",
        'payload = "SEL" + "ECT 1"; db.execute(payload)',
        'source = "SELECT 1"; payload = source; self.db.execute(payload)',
        'runner = getattr(db, "execute"); runner(make_statement())',
        'getattr(db, "execute")("SELECT 1")',
        "q = sql.SQL(template).format(sql.Identifier(name)); db.execute(q)",
    ],
)
def test_sql_ownership_policy_rejects_public_application_sql(tmp_path: Path, source: str) -> None:
    package = tmp_path / "example_insurance"
    package.mkdir()
    leaked = package / "renewal_policy.py"
    leaked.write_text(source + "\n", encoding="utf-8")
    role = next(role for role in PACKAGE_ROLES if role.distribution == "example-insurance")

    assert role_sql_ownership_violations(role, (leaked,)) == (
        "example-insurance contains SQL outside approved persistence owner renewal_policy.py:1",
    )


def test_runtime_declares_private_sql_owners_and_rejects_public_sql(tmp_path: Path) -> None:
    role = next(role for role in PACKAGE_ROLES if role.distribution == "openmagic-runtime")
    assert role.sql_owner_roots

    package = tmp_path / "openmagic_runtime"
    package.mkdir()
    leaked = package / "control.py"
    leaked.write_text('self.db.execute("LOCK TABLE authority")\n', encoding="utf-8")

    assert role_sql_ownership_violations(role, (leaked,)) == (
        "openmagic-runtime contains SQL outside approved persistence owner control.py:1",
    )


def test_runtime_transaction_owners_do_not_import_public_control_facades() -> None:
    runtime = ROOT / "packages/openmagic-runtime/src/openmagic_runtime"
    owners = (
        runtime / "_persistence/delivery_control.py",
        runtime / "kernel/_persistence/control_records.py",
        runtime / "kernel/_persistence/work_records.py",
    )

    imports = python_imports(owners)

    assert "openmagic_runtime.delivery" not in imports
    assert "openmagic_runtime.kernel.control" not in imports
    assert "openmagic_runtime.kernel.work" not in imports


def test_sql_ownership_policy_allows_non_sql_execute_protocol(tmp_path: Path) -> None:
    package = tmp_path / "openmagic_runtime"
    package.mkdir()
    execution = package / "execution.py"
    execution.write_text("executor.run(execution, cancellation)\n", encoding="utf-8")
    role = next(role for role in PACKAGE_ROLES if role.distribution == "openmagic-runtime")

    assert role_sql_ownership_violations(role, (execution,)) == ()


def test_public_renewal_policy_does_not_import_private_persistence_records() -> None:
    application_root = ROOT / "reference-apps/example-insurance/src/example_insurance"
    imports = python_imports((application_root / "renewal_decisions.py",))

    assert not any(name.startswith("example_insurance._persistence") for name in imports)


def test_runtime_root_and_role_modules_have_explicit_export_allowlists() -> None:
    assert openmagic_runtime.__all__ == ["__version__"]
    assert example_insurance.__all__ == ["__version__"]
    assert openmagic_api.__all__ == ["__version__"]
    assert openmagic_evals.__all__ == ["__version__"]
    assert "PlaygroundDeployment" in openmagic_playground.__all__

    forbidden_exports = {
        "Session",
        "Repository",
        "MigrationBundle",
        "Connection",
        "Model",
        "Row",
    }
    for module_name in RUNTIME_PUBLIC_MODULES:
        module = importlib.import_module(module_name)
        assert isinstance(module.__all__, list), module_name
        assert forbidden_exports.isdisjoint(module.__all__), module_name


def test_legacy_implementation_and_compatibility_paths_are_absent() -> None:
    assert not any((ROOT / "server").rglob("*.py"))
    assert not any(
        path
        for path in (ROOT / "web").rglob("*.ts")
        if not {"node_modules", ".next"}.intersection(path.parts)
    )
    assert not any(
        path
        for path in (ROOT / "web").rglob("*.tsx")
        if not {"node_modules", ".next"}.intersection(path.parts)
    )
    assert not (ROOT / "alembic.ini").exists()

    production_roots = (
        ROOT / "packages/openmagic-runtime/src",
        ROOT / "reference-apps/example-insurance/src",
        ROOT / "apps/api/src",
    )
    legacy_import = "server" + ".workflows"
    for source_root in production_roots:
        for path in source_root.rglob("*.py"):
            assert legacy_import not in path.read_text(encoding="utf-8")


def test_application_sql_does_not_reference_private_runtime_tables() -> None:
    application_root = ROOT / "reference-apps/example-insurance/src/example_insurance"
    violations: list[str] = []
    for path in application_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "openmagic_runtime." in node.value
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert violations == []


def test_verification_persistence_has_canonical_sql_owners_and_named_decoders() -> None:
    application_root = ROOT / "reference-apps/example-insurance/src/example_insurance"
    persistence_root = application_root / "_persistence"
    challenge_path = persistence_root / "verification_challenge_records.py"
    workflow_path = persistence_root / "verification_workflow_records.py"
    authority_path = persistence_root / "verification_authority_records.py"

    assert not tuple(application_root.glob("*_records.py"))

    assert "example_insurance.verification_workflows" not in challenge_path.read_text(
        encoding="utf-8"
    )
    assert "example_insurance.verification_challenges" not in workflow_path.read_text(
        encoding="utf-8"
    )

    positional_decodes: list[str] = []
    for path in (challenge_path, workflow_path, authority_path):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, int)
            ):
                positional_decodes.append(f"{path.name}:{node.lineno}")
    assert positional_decodes == []


def test_verification_authority_models_participation_and_roles_separately() -> None:
    migration = (
        ROOT
        / "reference-apps/example-insurance/src/example_insurance/_persistence/migrations"
        / "0004_deterministic_verification.sql"
    ).read_text(encoding="utf-8")
    participant_definition = migration.split(
        "CREATE TABLE example_insurance.workflow_participants (", 1
    )[1].split("CREATE TABLE example_insurance.workflow_role_assignments (", 1)[0]

    assert "role text" not in participant_definition
    assert "revoked_at" not in participant_definition
    assert "membership_id" not in participant_definition
    assert "UNIQUE (workflow_id, party_id)" in participant_definition
    assert "CREATE TABLE example_insurance.workflow_role_assignments" in migration


def test_worker_and_verification_control_depend_on_narrow_application_seams() -> None:
    application_root = ROOT / "reference-apps/example-insurance/src/example_insurance"
    worker_imports = _from_imports(application_root / "workflow_worker_control.py")
    request_imports = _from_imports(application_root / "verification_request_control.py")

    assert all(
        module
        not in {
            "example_insurance.renewal_attempt_control",
            "example_insurance.renewal_effects",
            "example_insurance.verification_attempt_control",
        }
        for module, _ in worker_imports
    )
    assert ("openmagic_runtime.threads", "ThreadStore") in request_imports
    assert ("openmagic_runtime.threads", "ThreadAccess") not in request_imports


def test_playground_safety_is_verified_through_its_process_interface() -> None:
    environment = {
        "PATH": os.defpath,
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(ROOT / "apps/playground/src"),
    }
    completed = subprocess.run(
        [sys.executable, "-m", "openmagic_playground", "manifest"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert json.loads(completed.stdout) == {
        "contributes_to_correctness": False,
        "deterministic_fixture_version": "issue-71.v1",
        "external_effects_enabled": False,
        "local_provider_only": True,
        "process_control": "explicit",
        "reset_requires_confirmation": True,
        "synthetic_data_only": True,
    }

    controls = subprocess.run(
        [sys.executable, "-m", "openmagic_playground", "controls"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert json.loads(controls.stdout) == {
        "actions": ["start", "drain", "reset", "restart", "stop"],
        "ownership": "explicit-local-processes",
        "roles": ["api", "workflow-worker", "delivery-worker"],
    }

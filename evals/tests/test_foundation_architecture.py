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
from openmagic_evals.evidence.package_policy import (
    PACKAGE_ROLES,
    project_dependencies,
    python_imports,
    role_dependency_violations,
    role_import_violations,
    role_private_import_violations,
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


def test_private_persistence_import_policy_rejects_full_import_paths(tmp_path: Path) -> None:
    fixture = tmp_path / "consumer.py"
    fixture.write_text(
        "import openmagic_runtime._persistence\nfrom example_insurance import _persistence\n",
        encoding="utf-8",
    )
    imports = python_imports((fixture,))
    api_role = next(role for role in PACKAGE_ROLES if role.distribution == "openmagic-api")

    assert "openmagic_runtime._persistence" in imports
    assert "example_insurance._persistence" in imports
    assert role_private_import_violations(api_role, imports) == (
        "openmagic-api imports private persistence package example_insurance._persistence",
        "openmagic-api imports private persistence package openmagic_runtime._persistence",
    )


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
    challenge_path = application_root / "verification_challenge_records.py"
    workflow_path = application_root / "verification_workflow_records.py"
    authority_path = application_root / "verification_authority_records.py"

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

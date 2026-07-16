"""Audit the actual installed wheel metadata and packaged Python surface."""

from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass
from importlib.metadata import Distribution, distribution
from pathlib import Path

from openmagic_evals.evidence.core_models import canonical_digest
from openmagic_evals.evidence.package_policy import (
    PACKAGE_ROLES,
    python_imports,
    requirement_name,
    role_dependency_violations,
    role_import_violations,
    role_private_import_violations,
    role_public_persistence_violations,
)
from openmagic_evals.evidence.surface_contracts import (
    APPLICATION_PUBLIC_EXPORTS,
    DELETED_IDENTIFIERS,
    EXPECTED_PRODUCTION_EDGES,
    PUBLIC_SURFACE_DIGESTS,
    RUNTIME_PUBLIC_EXPORTS,
)

_DISTRIBUTIONS = {role.distribution: role.package for role in PACKAGE_ROLES}


@dataclass(frozen=True)
class InstalledSurfaceAudit:
    passed: bool
    violations: tuple[str, ...]
    distributions: dict[str, str]
    production_dependency_edges: tuple[str, ...]
    private_persistence_packages: tuple[str, ...]
    audited_files: int


def _distribution_files(item: Distribution) -> tuple[Path, ...]:
    return tuple(
        Path(str(item.locate_file(file)))
        for file in (item.files or ())
        if file.parts and file.suffix in {".py", ".sql"}
    )


def _installed_public_exports(
    paths: tuple[Path, ...], package_name: str
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for path in paths:
        if path.suffix != ".py" or package_name not in path.parts:
            continue
        package_index = path.parts.index(package_name)
        relative = Path(*path.parts[package_index + 1 :])
        if any(part.startswith("_") for part in relative.parent.parts) or (
            relative.name != "__init__.py" and relative.name.startswith("_")
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        exports: tuple[str, ...] | None = None
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if not any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in targets
            ):
                continue
            if isinstance(node.value, (ast.List, ast.Tuple)):
                exports = tuple(
                    sorted(
                        element.value
                        for element in node.value.elts
                        if isinstance(element, ast.Constant) and isinstance(element.value, str)
                    )
                )
        if exports is None:
            raise ValueError(f"installed public module has no explicit exports: {path}")
        result[relative.as_posix()] = exports
    return dict(sorted(result.items()))


def audit_installed_environment() -> InstalledSurfaceAudit:
    installed = {name: distribution(name) for name in _DISTRIBUTIONS}
    files = {name: _distribution_files(item) for name, item in installed.items()}
    violations: list[str] = []
    for name, paths in files.items():
        expected_package = _DISTRIBUTIONS[name]
        unexpected = tuple(path for path in paths if expected_package not in path.parts)
        if unexpected:
            violations.append(f"installed distribution contains an unexpected package: {name}")
    internal = set(_DISTRIBUTIONS)
    edges = tuple(
        sorted(
            f"{owner} -> {dependency}"
            for owner in ("openmagic-runtime", "example-insurance", "openmagic-api")
            for dependency in {
                requirement_name(requirement) for requirement in (installed[owner].requires or ())
            }
            & internal
        )
    )
    if edges != EXPECTED_PRODUCTION_EDGES:
        violations.append("installed production dependency graph differs from the accepted graph")

    imports_by_distribution = {
        role.distribution: python_imports(files[role.distribution]) for role in PACKAGE_ROLES
    }
    for role in PACKAGE_ROLES:
        violations.extend(role_import_violations(role, imports_by_distribution[role.distribution]))
        violations.extend(role_public_persistence_violations(role, files[role.distribution]))
        violations.extend(
            role_private_import_violations(
                role,
                imports_by_distribution[role.distribution],
            )
        )
        requirements = frozenset(
            requirement_name(requirement)
            for requirement in (installed[role.distribution].requires or ())
        )
        violations.extend(role_dependency_violations(role, requirements))
        exports = _installed_public_exports(
            files[role.distribution],
            role.package,
        )
        if canonical_digest(exports) != PUBLIC_SURFACE_DIGESTS[role.distribution]:
            violations.append(
                f"installed {role.distribution} public modules or exports differ from exact surface"
            )
    application_imports = imports_by_distribution["example-insurance"]
    api_imports = imports_by_distribution["openmagic-api"]
    if any(name.startswith("openmagic_runtime._persistence") for name in application_imports):
        violations.append("installed application imports private runtime persistence")
    if any("._persistence" in name for name in api_imports):
        violations.append("installed API imports private persistence")

    runtime = importlib.import_module("openmagic_runtime")
    if getattr(runtime, "__all__", None) != ["__version__"]:
        violations.append("installed runtime root exports more than package metadata")
    if (
        _installed_public_exports(files["openmagic-runtime"], "openmagic_runtime")
        != RUNTIME_PUBLIC_EXPORTS
    ):
        violations.append("installed runtime modules or exports differ from the exact surface")
    if (
        _installed_public_exports(files["example-insurance"], "example_insurance")
        != APPLICATION_PUBLIC_EXPORTS
    ):
        violations.append("installed application modules or exports differ from the exact surface")

    all_paths = tuple(path for paths in files.values() for path in paths)
    for path in all_paths:
        source = path.read_text(encoding="utf-8")
        for identifier in DELETED_IDENTIFIERS:
            if identifier in source:
                violations.append(f"installed wheel retains a deleted identifier: {path.name}")
    private_packages = (
        "example_insurance._persistence",
        "openmagic_runtime._persistence",
    )
    for package in private_packages:
        prefix = package.replace(".", "/") + "/"
        owner = "example-insurance" if package.startswith("example") else "openmagic-runtime"
        packaged = installed[owner].files or ()
        if not any(str(file).startswith(prefix) for file in packaged):
            violations.append(
                f"installed wheel is missing its private persistence owner: {package}"
            )

    return InstalledSurfaceAudit(
        passed=not violations,
        violations=tuple(sorted(set(violations))),
        distributions={name: item.version for name, item in installed.items()},
        production_dependency_edges=edges,
        private_persistence_packages=private_packages,
        audited_files=len(all_paths),
    )


__all__ = ["InstalledSurfaceAudit", "audit_installed_environment"]

"""Audit the actual installed wheel metadata and packaged Python surface."""

from __future__ import annotations

import ast
import importlib
import re
from dataclasses import dataclass
from importlib.metadata import Distribution, distribution
from pathlib import Path

from openmagic_evals.evidence.surface_contracts import (
    DELETED_IDENTIFIERS,
    EXPECTED_PRODUCTION_EDGES,
    FORBIDDEN_EXPORTS,
    RUNTIME_PUBLIC_MODULES,
)

_DISTRIBUTIONS = {
    "openmagic-runtime": "openmagic_runtime",
    "example-insurance": "example_insurance",
    "openmagic-api": "openmagic_api",
    "openmagic-evals": "openmagic_evals",
}
_NORMALIZE = re.compile(r"[-_.]+")


@dataclass(frozen=True)
class InstalledSurfaceAudit:
    passed: bool
    violations: tuple[str, ...]
    distributions: dict[str, str]
    production_dependency_edges: tuple[str, ...]
    private_persistence_packages: tuple[str, ...]
    audited_files: int


def _normalized(value: str) -> str:
    return _NORMALIZE.sub("-", value).lower()


def _requirement_name(requirement: str) -> str:
    return _normalized(re.split(r"[ ;(<>=!~\[]", requirement, maxsplit=1)[0])


def _package_files(item: Distribution, package: str) -> tuple[Path, ...]:
    return tuple(
        Path(str(item.locate_file(file)))
        for file in (item.files or ())
        if file.parts and file.parts[0] == package and file.suffix in {".py", ".sql"}
    )


def _imports(paths: tuple[Path, ...]) -> set[str]:
    imported: set[str] = set()
    for path in paths:
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    return imported


def audit_installed_environment() -> InstalledSurfaceAudit:
    installed = {name: distribution(name) for name in _DISTRIBUTIONS}
    files = {name: _package_files(item, _DISTRIBUTIONS[name]) for name, item in installed.items()}
    violations: list[str] = []
    internal = set(_DISTRIBUTIONS)
    edges = tuple(
        sorted(
            f"{owner} -> {dependency}"
            for owner in ("openmagic-runtime", "example-insurance", "openmagic-api")
            for dependency in {
                _requirement_name(requirement) for requirement in (installed[owner].requires or ())
            }
            & internal
        )
    )
    if edges != EXPECTED_PRODUCTION_EDGES:
        violations.append("installed production dependency graph differs from the accepted graph")

    runtime_imports = _imports(files["openmagic-runtime"])
    application_imports = _imports(files["example-insurance"])
    api_imports = _imports(files["openmagic-api"])
    if any(
        name.startswith(("example_insurance", "openmagic_api", "openmagic_evals"))
        for name in runtime_imports
    ):
        violations.append("installed runtime imports an outward distribution")
    if any(name.startswith(("openmagic_api", "openmagic_evals")) for name in application_imports):
        violations.append("installed application imports an outward distribution")
    if any(name.startswith(("openmagic_evals", "openmagic_playground")) for name in api_imports):
        violations.append("installed API imports evidence or demonstration code")

    runtime = importlib.import_module("openmagic_runtime")
    if getattr(runtime, "__all__", None) != ["__version__"]:
        violations.append("installed runtime root exports more than package metadata")
    for relative in RUNTIME_PUBLIC_MODULES:
        module_name = "openmagic_runtime." + relative.removesuffix(".py").replace("/", ".")
        module = importlib.import_module(module_name)
        exports = getattr(module, "__all__", None)
        if not isinstance(exports, list):
            violations.append(
                f"installed runtime public module lacks explicit exports: {module_name}"
            )
        elif FORBIDDEN_EXPORTS.intersection(exports):
            violations.append(f"installed runtime exports persistence details: {module_name}")

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

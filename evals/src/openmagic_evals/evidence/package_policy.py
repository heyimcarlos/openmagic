"""Canonical package roles and import-direction scanner for every audit surface."""

from __future__ import annotations

import ast
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_NORMALIZE = re.compile(r"[-_.]+")


@dataclass(frozen=True)
class PackageRole:
    distribution: str
    package: str
    project: Path
    source: Path
    allowed_internal: frozenset[str]
    declared_internal_dependencies: frozenset[str]


PACKAGE_ROLES: tuple[PackageRole, ...] = (
    PackageRole(
        distribution="openmagic-runtime",
        package="openmagic_runtime",
        project=Path("packages/openmagic-runtime/pyproject.toml"),
        source=Path("packages/openmagic-runtime/src/openmagic_runtime"),
        allowed_internal=frozenset(),
        declared_internal_dependencies=frozenset(),
    ),
    PackageRole(
        distribution="example-insurance",
        package="example_insurance",
        project=Path("reference-apps/example-insurance/pyproject.toml"),
        source=Path("reference-apps/example-insurance/src/example_insurance"),
        allowed_internal=frozenset({"openmagic_runtime"}),
        declared_internal_dependencies=frozenset({"openmagic-runtime"}),
    ),
    PackageRole(
        distribution="openmagic-api",
        package="openmagic_api",
        project=Path("apps/api/pyproject.toml"),
        source=Path("apps/api/src/openmagic_api"),
        allowed_internal=frozenset({"example_insurance", "openmagic_runtime"}),
        declared_internal_dependencies=frozenset({"example-insurance", "openmagic-runtime"}),
    ),
    PackageRole(
        distribution="openmagic-playground",
        package="openmagic_playground",
        project=Path("apps/playground/pyproject.toml"),
        source=Path("apps/playground/src/openmagic_playground"),
        allowed_internal=frozenset({"example_insurance", "openmagic_api", "openmagic_runtime"}),
        declared_internal_dependencies=frozenset(
            {"example-insurance", "openmagic-api", "openmagic-runtime"}
        ),
    ),
    PackageRole(
        distribution="openmagic-evals",
        package="openmagic_evals",
        project=Path("evals/pyproject.toml"),
        source=Path("evals/src/openmagic_evals"),
        allowed_internal=frozenset(
            {
                "example_insurance",
                "openmagic_api",
                "openmagic_playground",
                "openmagic_runtime",
            }
        ),
        declared_internal_dependencies=frozenset(
            {
                "example-insurance",
                "openmagic-api",
                "openmagic-playground",
                "openmagic-runtime",
            }
        ),
    ),
)


def normalize_distribution(value: str) -> str:
    return _NORMALIZE.sub("-", value).lower()


def requirement_name(requirement: str) -> str:
    return normalize_distribution(re.split(r"[ ;(<>=!~\[]", requirement, maxsplit=1)[0])


def python_imports(paths: tuple[Path, ...]) -> frozenset[str]:
    imported: set[str] = set()
    for path in paths:
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
    return frozenset(imported)


def source_python_files(source_root: Path) -> tuple[Path, ...]:
    return tuple(sorted(source_root.rglob("*.py")))


def project_dependencies(project: Path) -> frozenset[str]:
    document = tomllib.loads(project.read_text(encoding="utf-8"))
    return frozenset(
        requirement_name(dependency) for dependency in document["project"].get("dependencies", [])
    )


def role_import_violations(role: PackageRole, imports: frozenset[str]) -> tuple[str, ...]:
    internal_packages = frozenset(item.package for item in PACKAGE_ROLES)
    forbidden = (imports & internal_packages).difference(role.allowed_internal, {role.package})
    return tuple(
        f"{role.distribution} imports forbidden internal package {package}"
        for package in sorted(forbidden)
    )


def role_dependency_violations(role: PackageRole, dependencies: frozenset[str]) -> tuple[str, ...]:
    internal_distributions = frozenset(item.distribution for item in PACKAGE_ROLES)
    actual = dependencies & internal_distributions
    if actual == role.declared_internal_dependencies:
        return ()
    return (
        f"{role.distribution} internal dependencies differ: "
        f"expected {sorted(role.declared_internal_dependencies)!r}, got {sorted(actual)!r}",
    )


def package_role(distribution: str) -> PackageRole:
    for role in PACKAGE_ROLES:
        if role.distribution == distribution:
            return role
    raise KeyError(distribution)


__all__ = [
    "PACKAGE_ROLES",
    "PackageRole",
    "normalize_distribution",
    "package_role",
    "project_dependencies",
    "python_imports",
    "requirement_name",
    "role_dependency_violations",
    "role_import_violations",
    "source_python_files",
]

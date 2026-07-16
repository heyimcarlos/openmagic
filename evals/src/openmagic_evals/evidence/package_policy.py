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
    sql_owner_roots: tuple[Path, ...] = ()


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
        sql_owner_roots=(Path("_persistence"), Path("migrations.py"), Path("readiness.py")),
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
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(
                    f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"
                )
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
    imported_internal = frozenset(
        name.split(".", 1)[0] for name in imports if name.split(".", 1)[0] in internal_packages
    )
    forbidden = imported_internal.difference(role.allowed_internal, {role.package})
    return tuple(
        f"{role.distribution} imports forbidden internal package {package}"
        for package in sorted(forbidden)
    )


def role_private_import_violations(role: PackageRole, imports: frozenset[str]) -> tuple[str, ...]:
    package_owners = {item.package: item.distribution for item in PACKAGE_ROLES}
    private_packages: set[str] = set()
    for imported in imports:
        parts = imported.split(".")
        owner = package_owners.get(parts[0])
        if owner is None or owner == role.distribution:
            continue
        for index, part in enumerate(parts[1:], start=1):
            if part.startswith("_") and not (part.startswith("__") and part.endswith("__")):
                private_packages.add(".".join(parts[: index + 1]))
                break
    return tuple(
        f"{role.distribution} imports private package {private_package}"
        for private_package in sorted(private_packages)
    )


def role_public_persistence_violations(
    role: PackageRole, paths: tuple[Path, ...]
) -> tuple[str, ...]:
    """Reject transaction record adapters exposed as public package modules."""

    violations: list[str] = []
    for path in paths:
        if path.suffix != ".py" or role.package not in path.parts:
            continue
        package_index = path.parts.index(role.package)
        relative = Path(*path.parts[package_index + 1 :])
        is_private = any(part.startswith("_") for part in relative.parts)
        if not is_private and relative.name.endswith("_records.py"):
            violations.append(
                f"{role.distribution} exposes persistence adapter {relative.as_posix()}"
            )
    return tuple(sorted(violations))


def role_sql_ownership_violations(role: PackageRole, paths: tuple[Path, ...]) -> tuple[str, ...]:
    """Reject database call sites outside a distribution's declared SQL owners."""

    if not role.sql_owner_roots:
        return ()
    violations: set[str] = set()
    for path in paths:
        if path.suffix != ".py" or role.package not in path.parts:
            continue
        package_index = path.parts.index(role.package)
        relative = Path(*path.parts[package_index + 1 :])
        if any(
            relative == owner or relative.parts[: len(owner.parts)] == owner.parts
            for owner in role.sql_owner_roots
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_sql_boundary_call(node):
                violations.add(
                    f"{role.distribution} contains SQL outside approved persistence owner "
                    f"{relative.as_posix()}:{node.lineno}"
                )
    return tuple(sorted(violations))


def _is_sql_boundary_call(node: ast.Call) -> bool:
    function = node.func
    if not isinstance(function, ast.Attribute):
        return False
    if (
        function.attr == "SQL"
        and isinstance(function.value, ast.Name)
        and function.value.id == "sql"
    ):
        return True
    if function.attr == "cursor":
        return True
    if function.attr not in {"execute", "executemany"}:
        return False
    receiver = function.value
    if isinstance(receiver, ast.Name):
        name = receiver.id
    elif isinstance(receiver, ast.Attribute):
        name = receiver.attr
    else:
        return False
    return name in {"connection", "cursor"} or name.endswith(("_connection", "_cursor"))


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
    "role_private_import_violations",
    "role_public_persistence_violations",
    "role_sql_ownership_violations",
    "source_python_files",
]

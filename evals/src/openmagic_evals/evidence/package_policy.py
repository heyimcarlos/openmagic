"""Canonical package roles and import-direction scanner for every audit surface."""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
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
        sql_owner_roots=(
            Path("_persistence"),
            Path("_delivery_records.py"),
            Path("kernel/_attempt_guard.py"),
            Path("kernel/_closure.py"),
            Path("kernel/_control_support.py"),
            Path("kernel/_deferred.py"),
            Path("kernel/_evidence_records.py"),
            Path("kernel/_inspection_records.py"),
            Path("kernel/_records.py"),
            Path("kernel/_signals.py"),
            Path("kernel/_step_mutations.py"),
            Path("kernel/_trace.py"),
            Path("kernel/_transition_records.py"),
            Path("kernel/_persistence"),
        ),
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
        sql_owner_roots=(Path("deployment_observation.py"), Path("reset.py")),
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
        sql_owner_roots=(
            Path("evidence/_inspection_base.py"),
            Path("evidence/_inspection_demo.py"),
            Path("evidence/_inspection_durable_chain.py"),
            Path("evidence/_inspection_process.py"),
            Path("evidence/_inspection_race.py"),
            Path("evidence/audit.py"),
            Path("evidence/fault_injection.py"),
            Path("evidence/postgres_provenance.py"),
            Path("evidence/race_processes.py"),
            Path("harness/email_provider.py"),
            Path("harness/renewal_scenario.py"),
            Path("harness/verifier.py"),
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
        string_bindings = _string_bindings(tree)
        accessors = _attribute_accessor_aliases(tree)
        sql_callable_references = _sql_callable_references(tree, string_bindings, accessors)
        for node in ast.walk(tree):
            if not isinstance(node, ast.expr):
                continue
            is_boundary_call = isinstance(node, ast.Call) and _is_sql_boundary_call(
                node, sql_callable_references, string_bindings, accessors
            )
            if is_boundary_call or _is_sql_callable_reference(node, string_bindings, accessors):
                violations.add(
                    f"{role.distribution} contains SQL outside approved persistence owner "
                    f"{relative.as_posix()}:{node.lineno}"
                )
    return tuple(sorted(violations))


def _assignments(tree: ast.AST) -> tuple[tuple[tuple[ast.expr, ...], ast.expr], ...]:
    assignments: list[tuple[tuple[ast.expr, ...], ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.NamedExpr):
            assignments.append(((node.target,), node.value))
            continue
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = tuple(node.targets) if isinstance(node, ast.Assign) else (node.target,)
        assignments.append((targets, node.value))
        for target in targets:
            assignments.extend(_collection_assignments(target, node.value))
    return tuple(assignments)


def _collection_assignments(
    target: ast.expr, value: ast.expr
) -> tuple[tuple[tuple[ast.expr, ...], ast.expr], ...]:
    items: tuple[tuple[object, ast.expr], ...] = ()
    if isinstance(value, (ast.List, ast.Tuple)):
        items = tuple(enumerate(value.elts))
    elif isinstance(value, ast.Dict):
        items = tuple(
            (key.value, item)
            for key, item in zip(value.keys, value.values, strict=True)
            if isinstance(key, ast.Constant) and isinstance(key.value, (int, str))
        )
    return tuple(
        (
            (
                ast.Subscript(
                    value=target,
                    slice=ast.Constant(key),
                    ctx=ast.Store(),
                ),
            ),
            item,
        )
        for key, item in items
        if not isinstance(item, ast.Starred)
    )


def _string_bindings(tree: ast.AST) -> dict[str, frozenset[str]]:
    bindings: dict[str, set[str]] = {}
    changed = True
    while changed:
        changed = False
        for targets, value in _assignments(tree):
            resolved = _resolve_strings(value, bindings)
            if not resolved:
                continue
            for target in targets:
                reference = _expression_reference(target)
                if reference is None:
                    continue
                known = bindings.setdefault(reference, set())
                if not resolved.issubset(known):
                    known.update(resolved)
                    changed = True
    return {reference: frozenset(values) for reference, values in bindings.items()}


def _attribute_accessor_aliases(tree: ast.AST) -> dict[str, frozenset[int]]:
    accessors: dict[str, set[int]] = {
        "builtins.getattr": {1},
        "getattr": {1},
    }
    changed = True
    while changed:
        changed = False
        for targets, value in _assignments(tree):
            argument_indices = _accessor_argument_indices(value, accessors)
            if not argument_indices:
                continue
            for target in targets:
                target_reference = _expression_reference(target)
                if target_reference is None:
                    continue
                known = accessors.setdefault(target_reference, set())
                if not argument_indices.issubset(known):
                    known.update(argument_indices)
                    changed = True
    return {reference: frozenset(indices) for reference, indices in accessors.items()}


def _sql_callable_references(
    tree: ast.AST,
    string_bindings: Mapping[str, AbstractSet[str]],
    accessors: Mapping[str, AbstractSet[int]],
) -> frozenset[str]:
    references: set[str] = set()
    changed = True
    while changed:
        changed = False
        for targets, value in _assignments(tree):
            if not _is_execute_callable(
                value,
                frozenset(references),
                string_bindings,
                accessors,
                unknown_accessor_is_sql=True,
            ):
                continue
            for target in targets:
                reference = _expression_reference(target)
                if reference is not None and reference not in references:
                    references.add(reference)
                    changed = True
    return frozenset(references)


def _expression_reference(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _expression_reference(node.value)
        return f"{owner}.{node.attr}" if owner is not None else None
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        owner = _expression_reference(node.value)
        if owner is not None and isinstance(node.slice.value, (int, str)):
            return f"{owner}[{node.slice.value!r}]"
    return None


def _resolve_strings(node: ast.expr, bindings: Mapping[str, AbstractSet[str]]) -> frozenset[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return frozenset({node.value})
    reference = _expression_reference(node)
    if reference is not None:
        return frozenset(bindings.get(reference, ()))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_strings(node.left, bindings)
        right = _resolve_strings(node.right, bindings)
        return frozenset(first + second for first in left for second in right)
    return frozenset()


def _accessor_argument_indices(
    node: ast.expr, accessors: Mapping[str, AbstractSet[int]]
) -> frozenset[int]:
    reference = _expression_reference(node)
    indices = frozenset(accessors.get(reference or "", ()))
    if isinstance(node, ast.Attribute):
        if node.attr == "getattr":
            return frozenset({1})
        if node.attr == "__getattribute__":
            return frozenset({0})
    if isinstance(node, ast.IfExp):
        return _accessor_argument_indices(node.body, accessors) | _accessor_argument_indices(
            node.orelse, accessors
        )
    if isinstance(node, ast.BoolOp):
        return frozenset().union(
            *(_accessor_argument_indices(value, accessors) for value in node.values)
        )
    return indices


def _accessed_attributes(
    node: ast.expr, accessors: Mapping[str, AbstractSet[int]]
) -> tuple[bool, tuple[ast.expr, ...], bool]:
    if not isinstance(node, ast.Call):
        return False, (), False
    argument_indices = _accessor_argument_indices(node.func, accessors)
    attributes: list[ast.expr] = []
    unresolved = False
    for argument_index in argument_indices:
        if len(node.args) <= argument_index or isinstance(node.args[argument_index], ast.Starred):
            unresolved = True
        else:
            attributes.append(node.args[argument_index])
    return bool(argument_indices), tuple(attributes), unresolved


def _is_execute_callable(
    node: ast.expr,
    references: frozenset[str],
    string_bindings: Mapping[str, AbstractSet[str]],
    accessors: Mapping[str, AbstractSet[int]],
    *,
    unknown_accessor_is_sql: bool = False,
) -> bool:
    if isinstance(node, ast.NamedExpr):
        return _is_execute_callable(
            node.value,
            references,
            string_bindings,
            accessors,
            unknown_accessor_is_sql=unknown_accessor_is_sql,
        )
    reference = _expression_reference(node)
    if reference is not None and reference in references:
        return True
    if isinstance(node, ast.Attribute):
        return node.attr in {"execute", "executemany"}
    is_accessor, attributes, unresolved = _accessed_attributes(node, accessors)
    if not is_accessor:
        return False
    resolved_names = frozenset().union(
        *(_resolve_strings(attribute, string_bindings) for attribute in attributes)
    )
    return bool(resolved_names & {"execute", "executemany"}) or (
        unknown_accessor_is_sql and (unresolved or not resolved_names)
    )


def _is_sql_callable_reference(
    node: ast.AST,
    string_bindings: Mapping[str, AbstractSet[str]],
    accessors: Mapping[str, AbstractSet[int]],
) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr in {"execute", "executemany"}
    return isinstance(node, ast.Call) and _is_execute_callable(
        node, frozenset(), string_bindings, accessors
    )


def _is_sql_boundary_call(
    node: ast.Call,
    sql_callable_references: frozenset[str],
    string_bindings: Mapping[str, AbstractSet[str]],
    accessors: Mapping[str, AbstractSet[int]],
) -> bool:
    function = node.func
    if _is_execute_callable(
        function,
        sql_callable_references,
        string_bindings,
        accessors,
        unknown_accessor_is_sql=True,
    ):
        return True
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
    return function.attr in {"execute", "executemany"}


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

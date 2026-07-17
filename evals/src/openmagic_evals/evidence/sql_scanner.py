"""Fail-closed AST scanner for SQL outside declared persistence owners."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from pathlib import Path


def sql_ownership_violations(
    *,
    distribution: str,
    package: str,
    owner_roots: tuple[Path, ...],
    paths: tuple[Path, ...],
) -> tuple[str, ...]:
    violations: set[str] = set()
    for path in paths:
        if path.suffix != ".py" or package not in path.parts:
            continue
        package_index = path.parts.index(package)
        relative = Path(*path.parts[package_index + 1 :])
        if any(
            relative == owner or relative.parts[: len(owner.parts)] == owner.parts
            for owner in owner_roots
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
                    f"{distribution} contains SQL outside approved persistence owner "
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


__all__ = ["sql_ownership_violations"]

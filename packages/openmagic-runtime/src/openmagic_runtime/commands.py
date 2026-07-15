"""Typed Command Runtime with canonical PostgreSQL idempotency."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from types import UnionType
from typing import Any, Generic, Literal, TypeVar, Union, get_args, get_origin, get_type_hints
from uuid import UUID

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest, canonical_value


@dataclass(frozen=True)
class Actor:
    kind: Literal["party", "system"]
    identifier: str


@dataclass(frozen=True)
class Cause:
    kind: Literal["message", "command", "schedule", "attempt"]
    identifier: str


ResultT = TypeVar("ResultT")
CommandT = TypeVar("CommandT")


@dataclass(frozen=True)
class CommandReceipt(Generic[ResultT]):
    command_id: UUID
    command_type: str
    schema_version: int
    command_digest: str
    result: ResultT
    result_digest: str
    committed_at: datetime


class CommandError(RuntimeError):
    code = "command_error"


class InvalidCommand(CommandError):
    code = "invalid_command"


class CommandUnavailable(CommandError):
    code = "command_unavailable"


class IdempotencyConflict(CommandError):
    code = "idempotency_conflict"


class StateConflict(CommandError):
    code = "state_conflict"


@dataclass(frozen=True)
class _Registration(Generic[CommandT, ResultT]):
    command_type: str
    schema_version: int
    command_class: type[CommandT]
    result_class: type[ResultT]
    handler: Callable[[CommandT, Connection[tuple[Any, ...]]], ResultT]
    result_decoder: Callable[[dict[str, Any]], ResultT]
    validator: Callable[[CommandT], None]


def _validate_value(value: Any, contract: Any, path: str) -> None:
    if contract is Any:
        return
    origin = get_origin(contract)
    arguments = get_args(contract)
    if origin is Literal:
        if value not in arguments:
            raise TypeError(f"{path} is outside its literal contract")
        return
    if origin in (UnionType, Union):
        if not any(_is_valid(value, option, path) for option in arguments):
            raise TypeError(f"{path} does not match any allowed type")
        return
    if origin is tuple:
        if not isinstance(value, tuple):
            raise TypeError(f"{path} must be a tuple")
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            for index, item in enumerate(value):
                _validate_value(item, arguments[0], f"{path}[{index}]")
        elif len(value) != len(arguments):
            raise TypeError(f"{path} tuple length does not match its contract")
        else:
            for index, (item, item_contract) in enumerate(zip(value, arguments, strict=True)):
                _validate_value(item, item_contract, f"{path}[{index}]")
        return
    if origin is dict:
        if not isinstance(value, dict):
            raise TypeError(f"{path} must be a dictionary")
        for key, item in value.items():
            _validate_value(key, arguments[0], f"{path}.key")
            _validate_value(item, arguments[1], f"{path}[{key!r}]")
        return
    if isinstance(contract, type) and is_dataclass(contract):
        if type(value) is not contract:
            raise TypeError(f"{path} must be {contract.__name__}")
        hints = get_type_hints(contract)
        for field in fields(contract):
            _validate_value(getattr(value, field.name), hints[field.name], f"{path}.{field.name}")
        return
    if contract is int and (not isinstance(value, int) or isinstance(value, bool)):
        raise TypeError(f"{path} must be an integer")
    if contract is not int and isinstance(contract, type) and not isinstance(value, contract):
        raise TypeError(f"{path} must be {contract.__name__}")


def _is_valid(value: Any, contract: Any, path: str) -> bool:
    try:
        _validate_value(value, contract, path)
    except TypeError:
        return False
    return True


class CommandRegistryBuilder:
    def __init__(self) -> None:
        self._registrations: dict[tuple[str, int], _Registration[Any, Any]] = {}

    def register(
        self,
        *,
        command_type: str,
        schema_version: int,
        command_class: type[CommandT],
        result_class: type[ResultT],
        handler: Callable[[CommandT, Connection[tuple[Any, ...]]], ResultT],
        result_decoder: Callable[[dict[str, Any]], ResultT],
        validator: Callable[[CommandT], None] = lambda command: None,
    ) -> CommandRegistryBuilder:
        key = (command_type, schema_version)
        if (
            key in self._registrations
            or schema_version <= 0
            or re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", command_type) is None
        ):
            raise ValueError(f"invalid or duplicate Command registration: {key}")
        self._registrations[key] = _Registration(
            command_type=command_type,
            schema_version=schema_version,
            command_class=command_class,
            result_class=result_class,
            handler=handler,
            result_decoder=result_decoder,
            validator=validator,
        )
        return self

    def build(self) -> dict[tuple[str, int], _Registration[Any, Any]]:
        return dict(self._registrations)


class CommandDispatcher:
    def __init__(
        self,
        *,
        database_url: str,
        registrations: dict[tuple[str, int], _Registration[Any, Any]],
    ) -> None:
        self._database_url = database_url
        self._registrations = dict(registrations)

    def execute(
        self,
        *,
        command_type: str,
        schema_version: int,
        command: CommandT,
    ) -> CommandReceipt[Any]:
        registration = self._registrations.get((command_type, schema_version))
        if registration is None:
            raise CommandUnavailable(f"unregistered Command: {command_type}:{schema_version}")
        if not isinstance(command, registration.command_class):
            raise InvalidCommand("Command type does not match its registered contract")
        try:
            _validate_value(command, registration.command_class, "command")
            registration.validator(command)
        except (TypeError, ValueError) as error:
            raise InvalidCommand(str(error)) from error
        command_id = getattr(command, "command_id", None)
        if not isinstance(command_id, UUID):
            raise InvalidCommand("Command ID must be a UUID")
        content = canonical_value(command)
        del content["command_id"]
        digest = canonical_digest(content)

        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (str(command_id),),
            )
            existing = connection.execute(
                "SELECT command_type, schema_version, command_digest, result, result_digest, "
                "committed_at FROM openmagic_runtime.command_receipts WHERE command_id = %s "
                "FOR UPDATE",
                (command_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing[0] != command_type
                    or existing[1] != schema_version
                    or existing[2] != digest
                ):
                    raise IdempotencyConflict("Command ID was already committed with other content")
                decoded_result = registration.result_decoder(dict(existing[3]))
                try:
                    _validate_value(decoded_result, registration.result_class, "result")
                except TypeError as error:
                    raise InvalidCommand(str(error)) from error
                return CommandReceipt(
                    command_id=command_id,
                    command_type=str(existing[0]),
                    schema_version=int(existing[1]),
                    command_digest=str(existing[2]),
                    result=decoded_result,
                    result_digest=str(existing[4]),
                    committed_at=existing[5],
                )

            result = registration.handler(command, connection)
            try:
                _validate_value(result, registration.result_class, "result")
            except TypeError as error:
                raise InvalidCommand(str(error)) from error
            result_payload = canonical_value(result)
            result_digest = canonical_digest(result_payload)
            committed = connection.execute(
                "INSERT INTO openmagic_runtime.command_receipts "
                "(command_id, command_type, schema_version, command_digest, result, result_digest) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING committed_at",
                (
                    command_id,
                    command_type,
                    schema_version,
                    digest,
                    Jsonb(result_payload),
                    result_digest,
                ),
            ).fetchone()
            if committed is None:
                raise RuntimeError("PostgreSQL did not return a Command commit timestamp")
            return CommandReceipt(
                command_id=command_id,
                command_type=command_type,
                schema_version=schema_version,
                command_digest=digest,
                result=result,
                result_digest=result_digest,
                committed_at=committed[0],
            )


__all__ = [
    "Actor",
    "Cause",
    "CommandDispatcher",
    "CommandError",
    "CommandReceipt",
    "CommandRegistryBuilder",
    "CommandUnavailable",
    "IdempotencyConflict",
    "InvalidCommand",
    "StateConflict",
]

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

from openmagic_runtime._canonical import canonical_digest, canonical_value
from openmagic_runtime._persistence.command_records import (
    insert_command,
    lock_command,
    read_committed_result,
)


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


@dataclass(frozen=True)
class CommittedCommandResult:
    command_id: UUID
    command_type: str
    schema_version: int
    result: dict[str, Any]
    result_digest: str


def read_committed_command_result(
    connection: Connection[tuple[Any, ...]], command_id: UUID
) -> CommittedCommandResult | None:
    record = read_committed_result(connection, command_id)
    if record is None:
        return None
    return CommittedCommandResult(
        command_id=record.command_id,
        command_type=record.command_type,
        schema_version=record.schema_version,
        result=record.result,
        result_digest=record.result_digest,
    )


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

    def dispatch(
        self,
        *,
        command_type: str,
        schema_version: int,
        command: CommandT,
    ) -> CommandReceipt[Any]:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            return self.execute_on(
                connection,
                command_type=command_type,
                schema_version=schema_version,
                command=command,
            )

    def execute_on(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        command_type: str,
        schema_version: int,
        command: CommandT,
        prepare_first_execution: Callable[[Connection[tuple[Any, ...]]], None] | None = None,
    ) -> CommandReceipt[Any]:
        """Execute inside the caller-owned transaction, preparing only a fresh Command."""
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

        existing = lock_command(connection, command_id)
        if existing is not None:
            if (
                existing.command_type != command_type
                or existing.schema_version != schema_version
                or existing.command_digest != digest
            ):
                raise IdempotencyConflict("Command ID was already committed with other content")
            decoded_result = registration.result_decoder(existing.result)
            try:
                _validate_value(decoded_result, registration.result_class, "result")
            except TypeError as error:
                raise InvalidCommand(str(error)) from error
            return CommandReceipt(
                command_id=command_id,
                command_type=existing.command_type,
                schema_version=existing.schema_version,
                command_digest=existing.command_digest,
                result=decoded_result,
                result_digest=existing.result_digest,
                committed_at=existing.committed_at,
            )

        if prepare_first_execution is not None:
            prepare_first_execution(connection)
        result = registration.handler(command, connection)
        try:
            _validate_value(result, registration.result_class, "result")
        except TypeError as error:
            raise InvalidCommand(str(error)) from error
        result_payload = canonical_value(result)
        result_digest = canonical_digest(result_payload)
        committed_at = insert_command(
            connection,
            command_id=command_id,
            command_type=command_type,
            schema_version=schema_version,
            command_digest=digest,
            result=result_payload,
            result_digest=result_digest,
        )
        return CommandReceipt(
            command_id=command_id,
            command_type=command_type,
            schema_version=schema_version,
            command_digest=digest,
            result=result,
            result_digest=result_digest,
            committed_at=committed_at,
        )


__all__ = [
    "Actor",
    "Cause",
    "CommandDispatcher",
    "CommandError",
    "CommandReceipt",
    "CommandRegistryBuilder",
    "CommandUnavailable",
    "CommittedCommandResult",
    "IdempotencyConflict",
    "InvalidCommand",
    "StateConflict",
    "read_committed_command_result",
]

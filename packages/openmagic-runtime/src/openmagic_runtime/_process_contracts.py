"""Public contracts for bounded owned-process cleanup."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol


class Closeable(Protocol):
    def close(self) -> object: ...


@dataclass(frozen=True)
class ProcessCleanup:
    reaped: bool
    errors: tuple[BaseException, ...]

    def raise_errors(self, message: str) -> None:
        if self.errors:
            raise BaseExceptionGroup(message, list(self.errors))


def finish_owned_cleanup(
    cleanup: Callable[[], object],
    *,
    execution_error: BaseException | None,
    message: str,
) -> None:
    """Complete cleanup without replacing a previously observed failure."""

    try:
        cleanup()
    except BaseException as cleanup_error:
        if execution_error is None:
            raise
        raise BaseExceptionGroup(message, [execution_error, cleanup_error]) from execution_error


@contextmanager
def owned_cleanup_scope(
    cleanup: Callable[[], object],
    *,
    message: str,
) -> Iterator[None]:
    """Preserve an active execution failure while completing owned cleanup."""

    execution_error: BaseException | None = None
    try:
        yield
    except BaseException as error:
        execution_error = error
        raise
    finally:
        finish_owned_cleanup(cleanup, execution_error=execution_error, message=message)


__all__ = [
    "Closeable",
    "ProcessCleanup",
    "finish_owned_cleanup",
    "owned_cleanup_scope",
]

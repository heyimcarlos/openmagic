"""One outer wall-clock bound for every public evidence runner."""

from __future__ import annotations

import inspect
import signal
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

Parameters = ParamSpec("Parameters")
Result = TypeVar("Result")


class EvidenceTimeout(TimeoutError):
    pass


def bounded_evidence(
    function: Callable[Parameters, Result],
) -> Callable[Parameters, Result]:
    signature = inspect.signature(function)

    @wraps(function)
    def run(*args: Parameters.args, **kwargs: Parameters.kwargs) -> Result:
        arguments = signature.bind(*args, **kwargs)
        arguments.apply_defaults()
        timeout_seconds = int(arguments.arguments["timeout_seconds"])
        previous_handler = signal.getsignal(signal.SIGALRM)

        def expire(_signum: int, _frame: object) -> None:
            raise EvidenceTimeout(
                f"evidence runner exceeded its {timeout_seconds}-second outer bound"
            )

        signal.signal(signal.SIGALRM, expire)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        try:
            return function(*args, **kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
            signal.signal(signal.SIGALRM, previous_handler)

    return run


__all__ = ["EvidenceTimeout", "bounded_evidence"]

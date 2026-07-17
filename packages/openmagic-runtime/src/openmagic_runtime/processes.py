"""Stable public boundary for owned local process trees."""

from openmagic_runtime._owned_process import OwnedProcess
from openmagic_runtime._process_contracts import (
    Closeable,
    ProcessCleanup,
    finish_owned_cleanup,
    owned_cleanup_scope,
)

__all__ = [
    "Closeable",
    "OwnedProcess",
    "ProcessCleanup",
    "finish_owned_cleanup",
    "owned_cleanup_scope",
]

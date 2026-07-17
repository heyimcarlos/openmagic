"""Private loopback network allocation for owned playground processes."""

from __future__ import annotations

import socket


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


__all__ = ["free_loopback_port"]

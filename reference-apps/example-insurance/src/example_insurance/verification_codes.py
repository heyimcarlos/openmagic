"""Restart-safe verification code derivation without persisted code material."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, repr=False)
class VerificationCodes:
    _secret: bytes

    def __post_init__(self) -> None:
        if len(self._secret) < 16:
            raise ValueError("Verification code secret must contain at least 16 bytes")

    def derive(self, challenge_id: UUID) -> str:
        digest = hmac.new(self._secret, challenge_id.bytes, hashlib.sha256).digest()
        return f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"

    def accepts(self, challenge_id: UUID, candidate: str) -> bool:
        return hmac.compare_digest(self.derive(challenge_id), candidate)


__all__ = ["VerificationCodes"]

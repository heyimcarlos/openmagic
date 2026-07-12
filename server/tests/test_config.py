from __future__ import annotations

import pytest

from server.config import Settings


def test_invalid_interaction_mode_environment_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENMAGIC_INTERACTION_MODE", "typo")

    with pytest.raises(ValueError, match="OPENMAGIC_INTERACTION_MODE"):
        Settings()

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from server.config import Settings


def test_invalid_interaction_mode_environment_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENMAGIC_INTERACTION_MODE", "typo")

    with pytest.raises(ValueError, match="OPENMAGIC_INTERACTION_MODE"):
        Settings()


def test_default_cors_does_not_trust_a_hostile_browser_origin():
    settings = Settings(cors_allow_origins_raw="")
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    with TestClient(app) as client:
        response = client.options(
            "/",
            headers={
                "Origin": "https://hostile.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.headers.get("access-control-allow-origin") is None

from fastapi.testclient import TestClient

from server.app import app
from server.config import Settings, get_settings


def test_backpressure_controls_require_explicit_demo_enablement() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        interaction_mode="workflow",
        enable_backpressure_demo=False,
    )
    try:
        response = TestClient(app).post(
            "/api/v1/demo/backpressure/workflows",
            json={"workflow_count": 5},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {
        "ok": False,
        "error": "The backpressure demo is not enabled",
    }

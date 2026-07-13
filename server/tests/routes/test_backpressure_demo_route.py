from datetime import UTC, datetime

from fastapi.testclient import TestClient

from server.app import app
from server.config import Settings, get_settings
from server.services import BackpressureDemoService, BackpressureSnapshot


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


def test_original_system_job_command_maps_to_renewal_workflows(monkeypatch) -> None:
    calls: list[tuple[int, str]] = []
    expected = BackpressureSnapshot.model_validate(
        {
            "captured_at": datetime.now(UTC),
            "worker": {
                "configured_job_concurrency": 0,
                "configured_notification_concurrency": 0,
                "job_worker_ids": (),
                "max_job_worker_capacity": 0,
            },
            "scope": {
                "visible_workflows": 0,
                "total_workflows": 0,
                "workflow_limit": 50,
                "truncated": False,
            },
            "latency": {
                "queue_claim_p50_ms": None,
                "execution_p50_ms": None,
                "notification_delivery_p50_ms": None,
                "end_to_end_p50_ms": None,
            },
            "counts": {
                "workflows": 0,
                "jobs": 0,
                "waiting": 0,
                "queued": 0,
                "running": 0,
                "succeeded": 0,
                "failed": 0,
                "cancelled": 0,
                "runs_running": 0,
                "runs_succeeded": 0,
                "runs_failed": 0,
                "notifications_queued": 0,
                "notifications_delivering": 0,
                "notifications_delivered": 0,
                "notifications_failed": 0,
                "completed_last_minute": 0,
                "oldest_queued_seconds": 0,
            },
            "jobs": (),
            "runs": (),
            "notifications": (),
            "approval_requests": (),
            "activity": (),
        }
    )

    async def enqueue_workflows(
        _service: BackpressureDemoService,
        workflow_count: int,
        scenario: str,
    ) -> BackpressureSnapshot:
        calls.append((workflow_count, scenario))
        return expected

    monkeypatch.setattr(BackpressureDemoService, "enqueue_workflows", enqueue_workflows)
    app.dependency_overrides[get_settings] = lambda: Settings(
        interaction_mode="workflow",
        enable_backpressure_demo=True,
        database_url="postgresql+psycopg://demo:demo@127.0.0.1:1/demo",
        workflow_broker_party_id="00000000-0000-0000-0000-000000000001",
        workflow_organization_party_id="00000000-0000-0000-0000-000000000002",
    )
    try:
        response = TestClient(app).post(
            "/api/v1/demo/backpressure/jobs",
            json={"job_count": 10},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert calls == [(5, "renewal")]

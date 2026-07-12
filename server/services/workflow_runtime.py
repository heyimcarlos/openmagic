"""Background delivery loop for durable Workflow Jobs and Notifications."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from functools import lru_cache
from uuid import UUID, uuid4

import sqlalchemy as sa

from server.agents.execution_agent.workflow_draft import FreshDraftExecutionAgentFactory
from server.agents.interaction_agent.workflow_notifications import (
    ConversationApprovalPresenter,
    FreshWorkflowInteractionFactory,
)
from server.config import Settings, get_settings
from server.logging_config import logger
from server.services.gmail import get_active_gmail_user_id
from server.workflows import (
    NotificationWorker,
    StaticWorkflowAuthority,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowRetrieval,
    WorkflowWorker,
    default_workflow_registry,
)
from server.workflows.email_adapter import (
    COMPOSIO_GMAIL_TOOLKIT_VERSION,
    ComposioGmailSendAdapter,
    ComposioMailboxBinding,
    EmailSendAdapter,
)
from server.workflows.identity_models import PartyIdentifierRow


class WorkflowRuntimeService:
    """Poll one Job and one Notification at a time in Workflow mode."""

    def __init__(self, settings: Settings, poll_interval_seconds: float = 1.0) -> None:
        self._settings = settings
        self._poll_interval = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._database: WorkflowDatabase | None = None
        self._job_worker: WorkflowWorker | None = None
        self._notification_worker: NotificationWorker | None = None

    async def start(self) -> None:
        if self._settings.interaction_mode != "workflow":
            return
        if self._task is not None and not self._task.done():
            return
        if not self._settings.database_url or not self._settings.workflow_cursor_secret:
            logger.warning(
                "Workflow runtime disabled because PostgreSQL configuration is incomplete"
            )
            return
        if not self._settings.workflow_broker_party_id:
            logger.warning("Workflow runtime disabled because Broker identity is incomplete")
            return
        if not self._settings.workflow_organization_party_id:
            logger.warning("Workflow runtime disabled because Organization identity is incomplete")
            return
        database = WorkflowDatabase(self._settings.database_url)
        control_plane = WorkflowControlPlane(
            database=database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(grants=set()),
        )
        retrieval = WorkflowRetrieval(
            database=database,
            cursor_secret=self._settings.workflow_cursor_secret.encode(),
        )
        self._database = database
        email_adapters = await self._email_adapters(database)
        self._job_worker = WorkflowWorker(
            control_plane=control_plane,
            executors={
                "renewal_email_drafter": FreshDraftExecutionAgentFactory(self._settings),
            },
            email_adapters=email_adapters,
            worker_id=f"workflow-worker:{uuid4()}",
            application_build=self._settings.app_version,
        )
        self._notification_worker = NotificationWorker(
            control_plane=control_plane,
            interactions=FreshWorkflowInteractionFactory(
                control_plane=control_plane,
                retrieval=retrieval,
                presenter=ConversationApprovalPresenter(
                    UUID(self._settings.workflow_broker_party_id)
                ),
                settings=self._settings,
                organization_party_id=UUID(self._settings.workflow_organization_party_id),
            ),
            worker_id=f"notification-worker:{uuid4()}",
        )
        self._running = True
        self._task = asyncio.create_task(self._run(), name="workflow-runtime")
        logger.info("Workflow runtime started", extra={"interval": self._poll_interval})

    async def _email_adapters(
        self,
        database: WorkflowDatabase,
    ) -> dict[str, EmailSendAdapter]:
        composio_user_id = self._settings.workflow_composio_user_id or get_active_gmail_user_id()
        if not self._settings.composio_api_key or not composio_user_id:
            logger.warning("Workflow Gmail adapter disabled because Composio is incomplete")
            return {}
        broker_party_id = UUID(self._settings.workflow_broker_party_id or "")
        async with database.read_transaction() as session:
            identifiers = (
                await session.scalars(
                    sa.select(PartyIdentifierRow).where(
                        PartyIdentifierRow.party_id == broker_party_id,
                        PartyIdentifierRow.kind == "email",
                        PartyIdentifierRow.verified_at.is_not(None),
                        PartyIdentifierRow.revoked_at.is_(None),
                    )
                )
            ).all()
        if len(identifiers) != 1:
            logger.warning("Workflow Gmail adapter requires one verified Broker mailbox")
            return {}
        from composio import Composio

        identifier = identifiers[0]
        client = Composio(
            api_key=self._settings.composio_api_key,
            toolkit_versions={"gmail": COMPOSIO_GMAIL_TOOLKIT_VERSION},
        )
        return {
            "composio_gmail_send": ComposioGmailSendAdapter(
                client=client,
                binding=ComposioMailboxBinding(
                    sender_mailbox_id=identifier.id,
                    expected_sender_address=identifier.value,
                    composio_user_id=composio_user_id,
                ),
            )
        }

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._database is not None:
            await self._database.dispose()
        self._database = None
        self._job_worker = None
        self._notification_worker = None

    async def _run(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - long-running service boundary
                logger.exception(
                    "Workflow runtime tick failed",
                    extra={"error_type": type(exc).__name__},
                )
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        if self._job_worker is None or self._notification_worker is None:
            return
        await self._job_worker.run_once()
        await self._notification_worker.run_once()


@lru_cache(maxsize=1)
def get_workflow_runtime_service() -> WorkflowRuntimeService:
    return WorkflowRuntimeService(get_settings())


__all__ = ["WorkflowRuntimeService", "get_workflow_runtime_service"]

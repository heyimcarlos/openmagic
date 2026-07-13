"""Background delivery loop for durable Workflow Jobs and Notifications."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from functools import lru_cache
from uuid import UUID, uuid4

from server.agents.execution_agent.workflow_draft import FreshDraftExecutionAgentFactory
from server.agents.interaction_agent.verification_resume import (
    VerificationDeliveryAttentionInteractionFactory,
    VerificationResumeInteractionFactory,
    VerificationResumeRecoveryInteractionFactory,
)
from server.agents.interaction_agent.workflow_notifications import (
    ConversationApprovalPresenter,
    FreshWorkflowInteractionFactory,
)
from server.config import Settings, get_settings
from server.logging_config import logger
from server.services.conversation import get_conversation_session
from server.workflows import (
    COMPOSIO_GMAIL_TOOLKIT_VERSION,
    VERIFICATION_DELIVERY_ATTENTION_NOTIFICATION_KIND,
    VERIFICATION_RESUME_NOTIFICATION_KIND,
    VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND,
    ComposioGmailSendAdapter,
    ComposioMailboxBinding,
    ComposioVerificationEmailSender,
    EmailSendAdapter,
    NotificationWorker,
    StaticWorkflowAuthority,
    StepUpVerification,
    VerificationEmailExecutionHandler,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowRetrieval,
    WorkflowWorker,
    default_workflow_registry,
    find_sms_party_by_id,
    resolve_verified_mailbox,
    sms_interaction_id,
)

from .workflow_worker_fleet import InProcessWorkflowWorkerFleet


class WorkflowRuntimeService:
    """Poll a local Worker fleet and durable Notification handlers."""

    def __init__(self, settings: Settings, poll_interval_seconds: float = 1.0) -> None:
        self._settings = settings
        self._poll_interval = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._database: WorkflowDatabase | None = None
        self._job_workers: InProcessWorkflowWorkerFleet | None = None
        self._notification_worker: NotificationWorker | None = None
        self._verification_resume_worker: NotificationWorker | None = None
        self._verification_recovery_worker: NotificationWorker | None = None
        self._verification_attention_worker: NotificationWorker | None = None

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
        broker_party_id = UUID(self._settings.workflow_broker_party_id)
        broker = await find_sms_party_by_id(database, broker_party_id)
        if broker is None:
            logger.warning("Workflow runtime disabled because Broker SMS identity is incomplete")
            await database.dispose()
            return
        conversation = get_conversation_session(sms_interaction_id(broker.phone)).log
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
        verification = None
        deterministic_handlers = {}
        if self._settings.verification_code_secret:
            verification = StepUpVerification(
                database=database,
                code_secret=self._settings.verification_code_secret.encode(),
                delivery_available=bool(
                    self._settings.composio_api_key and self._settings.workflow_composio_user_id
                ),
            )
        if (
            verification is not None
            and self._settings.composio_api_key
            and self._settings.workflow_composio_user_id
        ):
            from composio import Composio

            client = Composio(
                api_key=self._settings.composio_api_key,
                toolkit_versions={"gmail": COMPOSIO_GMAIL_TOOLKIT_VERSION},
            )
            deterministic_handlers["composio_verification_email"] = (
                VerificationEmailExecutionHandler(
                    verification=verification,
                    sender=ComposioVerificationEmailSender(
                        client=client,
                        composio_user_id=self._settings.workflow_composio_user_id,
                    ),
                )
            )
        self._job_workers = InProcessWorkflowWorkerFleet(
            lambda worker_id: WorkflowWorker(
                control_plane=control_plane,
                executors={
                    "renewal_email_drafter": FreshDraftExecutionAgentFactory(self._settings),
                },
                email_adapters=email_adapters,
                deterministic_handlers=deterministic_handlers,
                worker_id=worker_id,
                application_build=self._settings.app_version,
            )
        )
        self._notification_worker = NotificationWorker(
            control_plane=control_plane,
            interactions=FreshWorkflowInteractionFactory(
                control_plane=control_plane,
                retrieval=retrieval,
                presenter=ConversationApprovalPresenter(
                    broker_party_id,
                    conversation=conversation,
                ),
                settings=self._settings,
                organization_party_id=UUID(self._settings.workflow_organization_party_id),
                conversation=conversation,
            ),
            worker_id=f"notification-worker:{uuid4()}",
            notification_kinds=("approval_required", "send_confirmed"),
        )
        if verification is not None:
            self._verification_resume_worker = NotificationWorker(
                control_plane=control_plane,
                interactions=VerificationResumeInteractionFactory(
                    verification=verification,
                    settings=self._settings,
                ),
                worker_id=f"verification-resume-worker:{uuid4()}",
                notification_kinds=(VERIFICATION_RESUME_NOTIFICATION_KIND,),
            )
            self._verification_recovery_worker = NotificationWorker(
                control_plane=control_plane,
                interactions=VerificationResumeRecoveryInteractionFactory(
                    verification=verification,
                ),
                worker_id=f"verification-recovery-worker:{uuid4()}",
                notification_kinds=(VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND,),
            )
            self._verification_attention_worker = NotificationWorker(
                control_plane=control_plane,
                interactions=VerificationDeliveryAttentionInteractionFactory(
                    verification=verification,
                ),
                worker_id=f"verification-attention-worker:{uuid4()}",
                notification_kinds=(VERIFICATION_DELIVERY_ATTENTION_NOTIFICATION_KIND,),
            )
        if verification is None or not deterministic_handlers:
            logger.warning(
                "Verification email worker disabled because verification or Composio is incomplete"
            )
        self._running = True
        self._task = asyncio.create_task(self._run(), name="workflow-runtime")
        logger.info("Workflow runtime started", extra={"interval": self._poll_interval})

    async def _email_adapters(
        self,
        database: WorkflowDatabase,
    ) -> dict[str, EmailSendAdapter]:
        composio_user_id = self._settings.workflow_composio_user_id
        if not self._settings.composio_api_key or not composio_user_id:
            logger.warning("Workflow Gmail adapter disabled because Composio is incomplete")
            return {}
        broker_party_id = UUID(self._settings.workflow_broker_party_id or "")
        mailbox = await resolve_verified_mailbox(database, broker_party_id)
        if mailbox is None:
            logger.warning("Workflow Gmail adapter requires one verified Broker mailbox")
            return {}
        from composio import Composio

        client = Composio(
            api_key=self._settings.composio_api_key,
            toolkit_versions={"gmail": COMPOSIO_GMAIL_TOOLKIT_VERSION},
        )
        return {
            "composio_gmail_send": ComposioGmailSendAdapter(
                client=client,
                binding=ComposioMailboxBinding(
                    sender_mailbox_id=mailbox.id,
                    expected_sender_address=mailbox.address,
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
        self._job_workers = None
        self._notification_worker = None
        self._verification_resume_worker = None
        self._verification_recovery_worker = None
        self._verification_attention_worker = None

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
        if self._job_workers is None or self._notification_worker is None:
            return
        pollers = [
            self._job_workers.run_once(),
            self._notification_worker.run_once(),
        ]
        if self._verification_resume_worker is not None:
            pollers.append(self._verification_resume_worker.run_once())
        if self._verification_recovery_worker is not None:
            pollers.append(self._verification_recovery_worker.run_once())
        if self._verification_attention_worker is not None:
            pollers.append(self._verification_attention_worker.run_once())
        await asyncio.gather(*pollers)

    @property
    def job_worker_ids(self) -> tuple[str, ...]:
        return self._job_workers.worker_ids if self._job_workers is not None else ()

    @property
    def max_job_worker_capacity(self) -> int:
        return self._job_workers.max_capacity if self._job_workers is not None else 8

    def add_demo_worker(self) -> str:
        """Add real local claim capacity only through the explicitly enabled demo."""

        if not self._settings.enable_backpressure_demo:
            raise PermissionError("The backpressure demo is not enabled")
        if self._job_workers is None:
            raise RuntimeError("The Workflow runtime is not running")
        worker_id = self._job_workers.add_worker()
        logger.info(
            "Workflow demo worker added",
            extra={"worker_id": worker_id, "capacity": len(self._job_workers.worker_ids)},
        )
        return worker_id


@lru_cache(maxsize=1)
def get_workflow_runtime_service() -> WorkflowRuntimeService:
    return WorkflowRuntimeService(get_settings())


__all__ = ["WorkflowRuntimeService", "get_workflow_runtime_service"]

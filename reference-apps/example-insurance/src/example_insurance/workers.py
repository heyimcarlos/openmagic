"""Example Insurance composition for installed Worker process roles."""

from __future__ import annotations

import argparse
from pathlib import Path
from threading import Event
from uuid import uuid4

from openmagic_runtime.workers import WorkerRole, serve_worker

from example_insurance.renewals import ExampleInsurance


def _main(role: WorkerRole) -> None:
    parser = argparse.ArgumentParser(prog=f"openmagic-{role}")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--email-provider-url")
    parser.add_argument("--verification-code-secret-file")
    arguments = parser.parse_args()
    if role == "workflow-worker" and arguments.verification_code_secret_file is None:
        parser.error("Workflow Worker requires --verification-code-secret-file")
    verification_code_secret = (
        Path(arguments.verification_code_secret_file).read_bytes()
        if arguments.verification_code_secret_file is not None
        else None
    )
    application = ExampleInsurance(
        database_url=arguments.database_url,
        email_provider_url=arguments.email_provider_url,
        verification_code_secret=verification_code_secret,
    )
    if role == "workflow-worker":
        application.prepare_workflow_worker()
        claimed = None

        def tick(stop: Event) -> object:
            nonlocal claimed
            if claimed is None:
                application.recover_expired_workflow_attempt()
                claimed = application.claim_workflow_attempt(
                    worker_id=arguments.worker_id,
                    claim_request_id=uuid4(),
                )
                return claimed
            current = claimed
            claimed = None
            return application.complete_workflow_attempt(
                attempt=current,
                worker_id=arguments.worker_id,
                worker_shutdown=stop,
            )

    else:
        application.prepare()
        delivery_claim = None

        def tick(stop: Event) -> object:
            del stop
            nonlocal delivery_claim
            if delivery_claim is None:
                delivery_claim = application.claim_delivery_attempt(
                    worker_id=arguments.worker_id,
                    claim_request_id=uuid4(),
                )
                return delivery_claim
            current = delivery_claim
            delivery_claim = None
            return application.complete_delivery_attempt(
                claim=current,
                worker_id=arguments.worker_id,
            )

    serve_worker(
        role=role,
        database_url=arguments.database_url,
        host=arguments.host,
        port=arguments.port,
        worker_id=arguments.worker_id,
        tick=tick,
    )


def workflow_worker_main() -> None:
    _main("workflow-worker")


def delivery_worker_main() -> None:
    _main("delivery-worker")


__all__ = ["delivery_worker_main", "workflow_worker_main"]

"""HTTP contracts for durable renewal submission."""

from __future__ import annotations

from uuid import UUID

from example_insurance.renewals import (
    ExampleInsurance,
    RenewalFacts,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
)
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.threads import CreateThread, ThreadStore
from pydantic import BaseModel, ConfigDict, Field


class StartRenewalRequest(BaseModel):
    """Complete, replayable input for one durable renewal Command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: UUID
    workflow_id: UUID
    thread_id: UUID
    policy_id: UUID
    actor_id: UUID
    cause_id: UUID
    policy_number: str = Field(min_length=1)
    policyholder_name: str = Field(min_length=1)
    policyholder_email: str = Field(min_length=1)
    renewal_date: str = Field(min_length=1)
    expiring_premium_cents: int = Field(gt=0)


class StartRenewalResponse(BaseModel):
    """Durable identities returned by renewal submission or replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: UUID
    workflow_id: UUID
    instance_id: UUID
    thread_id: UUID


def submit_renewal(*, database_url: str, request: StartRenewalRequest) -> StartRenewalResponse:
    """Submit one renewal through the public application boundary."""

    threads = ThreadStore(database_url=database_url)
    try:
        thread = threads.read(request.thread_id)
    except KeyError:
        threads.create(
            CreateThread(
                thread_id=request.thread_id,
                channel_kind="email",
                channel_reference=request.policyholder_email,
            )
        )
    else:
        if thread.channel_kind != "email" or thread.channel_reference != request.policyholder_email:
            raise ValueError("Renewal replay changed its durable Thread identity")

    application = ExampleInsurance(database_url=database_url)
    application.prepare()
    application.replace_renewal_facts(
        RenewalFacts(
            policy_id=request.policy_id,
            policy_number=request.policy_number,
            policyholder_name=request.policyholder_name,
            policyholder_email=request.policyholder_email,
            renewal_date=request.renewal_date,
            expiring_premium_cents=request.expiring_premium_cents,
        )
    )
    receipt = application.start_renewal_outreach(
        StartRenewalOutreach(
            command_id=request.command_id,
            actor=Actor("party", str(request.actor_id)),
            cause=Cause("message", str(request.cause_id)),
            input=StartRenewalOutreachInput(
                workflow_id=request.workflow_id,
                thread_id=request.thread_id,
                policy_id=request.policy_id,
                policy_number=request.policy_number,
                policyholder_name=request.policyholder_name,
                policyholder_email=request.policyholder_email,
                renewal_date=request.renewal_date,
                expiring_premium_cents=request.expiring_premium_cents,
            ),
        )
    )
    return StartRenewalResponse(
        command_id=receipt.command_id,
        workflow_id=receipt.result.workflow_id,
        instance_id=receipt.result.instance_id,
        thread_id=receipt.result.thread_id,
    )


__all__ = ["StartRenewalRequest", "StartRenewalResponse", "submit_renewal"]

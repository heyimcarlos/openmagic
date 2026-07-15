"""Canonical application Domain Event lineage encoding."""

from openmagic_runtime.commands import Actor, Cause


def actor_record(actor: Actor) -> dict[str, str]:
    return {"kind": actor.kind, "identifier": actor.identifier}


def cause_record(cause: Cause) -> dict[str, str]:
    return {"kind": cause.kind, "identifier": cause.identifier}


__all__ = ["actor_record", "cause_record"]

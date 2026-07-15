"""Application-owned composition of Command handlers into the generic runtime."""

from openmagic_runtime.commands import CommandDispatcher, CommandRegistryBuilder

from example_insurance.renewal_registry import (
    RenewalCommandHandlers,
    register_renewal_commands,
)
from example_insurance.verification_registry import (
    VerificationCommandHandlers,
    register_verification_commands,
)


def application_command_dispatcher(
    *,
    database_url: str,
    renewal_handlers: RenewalCommandHandlers,
    verification_handlers: VerificationCommandHandlers,
) -> CommandDispatcher:
    builder = register_renewal_commands(CommandRegistryBuilder(), renewal_handlers)
    register_verification_commands(builder, verification_handlers)
    return CommandDispatcher(database_url=database_url, registrations=builder.build())


__all__ = ["application_command_dispatcher"]

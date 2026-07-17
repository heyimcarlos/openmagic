"""Canonical application Definition correlations for private evidence phases."""

from uuid import UUID

from example_insurance.renewal_definition import RENEWAL_DEFINITION
from example_insurance.verification_definition import VERIFICATION_DEFINITION

from openmagic_evals.evidence.core_models import InstanceDefinitionCorrelation


def renewal_instance_definition(instance_id: UUID) -> InstanceDefinitionCorrelation:
    return InstanceDefinitionCorrelation.from_identity(instance_id, RENEWAL_DEFINITION.identity)


def verification_instance_definition(instance_id: UUID) -> InstanceDefinitionCorrelation:
    return InstanceDefinitionCorrelation.from_identity(
        instance_id, VERIFICATION_DEFINITION.identity
    )


__all__ = ["renewal_instance_definition", "verification_instance_definition"]

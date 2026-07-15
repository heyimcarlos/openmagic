"""Synthetic-only foundation for the OpenMagic playground."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PlaygroundSafety:
    synthetic_data_only: bool = True
    external_effects_enabled: bool = False
    local_provider_only: bool = True
    deterministic_fixture_version: str = "issue-71.v1"
    process_control: str = "explicit"
    reset_requires_confirmation: bool = True
    contributes_to_correctness: bool = False

    def as_dict(self) -> dict[str, bool | str]:
        return asdict(self)


def safety_manifest() -> PlaygroundSafety:
    return PlaygroundSafety()


__all__ = ["PlaygroundSafety", "safety_manifest"]

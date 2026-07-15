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


@dataclass(frozen=True)
class PlaygroundProcessControls:
    roles: tuple[str, ...] = ("api", "workflow-worker", "delivery-worker")
    actions: tuple[str, ...] = ("start", "drain", "restart", "stop")
    ownership: str = "explicit-local-processes"

    def as_dict(self) -> dict[str, tuple[str, ...] | str]:
        return asdict(self)


def safety_manifest() -> PlaygroundSafety:
    return PlaygroundSafety()


def process_controls() -> PlaygroundProcessControls:
    return PlaygroundProcessControls()


__all__ = [
    "PlaygroundProcessControls",
    "PlaygroundSafety",
    "process_controls",
    "safety_manifest",
]

"""Synthetic-only foundation for the OpenMagic playground."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PlaygroundSafety:
    synthetic_data_only: bool = True
    external_effects_enabled: bool = False

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


def safety_manifest() -> PlaygroundSafety:
    return PlaygroundSafety()


__all__ = ["PlaygroundSafety", "safety_manifest"]

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MigrationBundle:
    owner: str
    schema: str
    resource_package: str

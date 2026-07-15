"""Private runtime persistence and packaged migration resources."""

from openmagic_runtime._persistence.bundle import MigrationBundle


def bundle() -> MigrationBundle:
    return MigrationBundle(
        owner="openmagic-runtime",
        schema="openmagic_runtime",
        resource_package="openmagic_runtime._persistence.migrations",
    )


__all__ = ["bundle"]

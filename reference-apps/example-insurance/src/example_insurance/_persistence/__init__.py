"""Private Example Insurance persistence and packaged migrations."""

from example_insurance._persistence.bundle import MigrationBundle


def bundle() -> MigrationBundle:
    return MigrationBundle(
        owner="example-insurance",
        schema="example_insurance",
        resource_package="example_insurance._persistence.migrations",
    )


__all__ = ["bundle"]

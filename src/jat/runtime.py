"""Application runtime initialization shared by JAT entrypoints."""

import truststore
from robocorp import log


def configure_runtime() -> None:
    """Use the host trust store and mark initialization in the Robocorp log."""
    truststore.inject_into_ssl()
    log.info("JAT runtime initialized")

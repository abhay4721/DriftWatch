"""Worker process entrypoint reserved for durable scan execution in Phase 5."""

import logging
import signal
import threading

from driftwatch.config import settings
from driftwatch.logging_config import configure_logging


def main() -> None:
    configure_logging(settings.log_level)
    logger = logging.getLogger("driftwatch.worker")
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    logger.info("Worker process is running; scan execution is introduced in Phase 5")
    while not stopping.wait(30):
        logger.debug("Worker process heartbeat")
    logger.info("Worker process stopped")


if __name__ == "__main__":
    main()

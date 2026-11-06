"""Durable worker process for queued infrastructure scans."""

import logging
import signal
import threading

from driftwatch.config import settings
from driftwatch.database import SessionLocal
from driftwatch.logging_config import configure_logging
from driftwatch.scan_service import claim_next_scan, execute_scan, fail_abandoned_scans


def main() -> None:
    configure_logging(settings.log_level)
    logger = logging.getLogger("driftwatch.worker")
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    abandoned = fail_abandoned_scans()
    if abandoned:
        logger.warning("Marked %s interrupted scan(s) as failed", abandoned)
    logger.info("Scan worker started; polling every %s seconds", settings.worker_poll_seconds)
    while not stopping.is_set():
        scan_id = None
        try:
            with SessionLocal() as db:
                scan_id = claim_next_scan(db)
            if scan_id is not None:
                logger.info("Claimed scan %s", scan_id)
                execute_scan(SessionLocal, scan_id)
                logger.info("Finished scan %s", scan_id)
                continue
        except Exception:
            logger.exception("Worker iteration failed")
        stopping.wait(max(1, settings.worker_poll_seconds))
    logger.info("Scan worker stopped")


if __name__ == "__main__":
    main()

"""Periodic scheduler that enqueues scans for enabled infrastructure sources."""

import logging
import signal
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from driftwatch.config import settings
from driftwatch.database import SessionLocal
from driftwatch.db_models import InfrastructureSource, Scan
from driftwatch.logging_config import configure_logging
from driftwatch.scan_service import enqueue_scan


def enqueue_due_scans() -> int:
    """Queue sources whose last scheduled scan is older than the configured interval."""
    now = datetime.now(UTC)
    interval = timedelta(seconds=max(60, settings.schedule_interval_seconds))
    queued = 0
    with SessionLocal() as db:
        sources = db.scalars(
            select(InfrastructureSource)
            .where(
                InfrastructureSource.enabled.is_(True),
                InfrastructureSource.provider == "opentofu",
            )
            .order_by(InfrastructureSource.created_at, InfrastructureSource.id)
        ).all()
        for source in sources:
            # Serialize schedulers on the source row and skip duplicate queued/running jobs.
            locked_source = db.scalar(
                select(InfrastructureSource)
                .where(InfrastructureSource.id == source.id)
                .with_for_update()
            )
            if locked_source is None or not locked_source.enabled:
                continue
            active_scan = db.scalar(
                select(Scan.id)
                .where(
                    Scan.source_id == locked_source.id,
                    Scan.status.in_(("queued", "running")),
                )
                .limit(1)
            )
            if active_scan is not None:
                db.rollback()
                continue
            latest = db.scalar(
                select(Scan)
                .where(
                    Scan.source_id == locked_source.id,
                    Scan.trigger_type == "scheduled",
                )
                .order_by(Scan.created_at.desc(), Scan.id.desc())
                .limit(1)
            )
            last_time = (latest.completed_at or latest.created_at) if latest else None
            if last_time is not None:
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=UTC)
                if now - last_time < interval:
                    db.rollback()
                    continue
            enqueue_scan(db, locked_source, "scheduled")
            db.commit()
            queued += 1
    return queued


def main() -> None:
    configure_logging(settings.log_level)
    logger = logging.getLogger("driftwatch.scheduler")
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    logger.info(
        "Scan scheduler started; interval=%s seconds, polling every %s seconds",
        settings.schedule_interval_seconds,
        settings.scheduler_poll_seconds,
    )
    while not stopping.is_set():
        try:
            count = enqueue_due_scans()
            if count:
                logger.info("Queued %s scheduled scan(s)", count)
        except Exception:
            logger.exception("Scheduler iteration failed")
        stopping.wait(max(5, settings.scheduler_poll_seconds))
    logger.info("Scan scheduler stopped")


if __name__ == "__main__":
    main()

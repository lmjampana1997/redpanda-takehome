"""Reasoning service entrypoint.

Milestone 5 (this state): connects to Redpanda as a consumer, reads from
wiki.edits.enriched, and logs what it receives. No LLM calls yet — those
land in milestones 6-9 (extraction, classification, escalation, write path).
"""

import logging
import signal
import threading

from app.config import load_settings
from app.consumer import build_consumer, iter_enriched_records

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("reasoning_service")


def main() -> None:
    settings = load_settings()
    stop_event = threading.Event()

    def handle_shutdown(signum: int, _frame: object) -> None:
        logger.info("Received signal %s, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    logger.info(
        "Starting reasoning service: brokers=%s topic=%s group=%s",
        settings.redpanda_brokers,
        settings.topic_enriched,
        settings.consumer_group_id,
    )

    consumer = build_consumer(settings)
    try:
        for key, record in iter_enriched_records(
            consumer, settings.topic_enriched, stop_event
        ):
            logger.info(
                "revision=%s title=%r comment=%r diff_fetch_ok=%s diff_len=%s",
                key,
                record.get("title"),
                record.get("comment"),
                record.get("diff_fetch_ok"),
                len(record.get("diff_html") or ""),
            )
    finally:
        consumer.close()
        logger.info("Consumer closed, exiting.")


if __name__ == "__main__":
    main()

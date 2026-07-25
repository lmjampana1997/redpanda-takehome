"""Thin wrapper around confluent_kafka for consuming wiki.edits.enriched."""

import json
import logging
import threading
from collections.abc import Iterator
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException

from app.config import Settings

logger = logging.getLogger(__name__)


def build_consumer(settings: Settings) -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": settings.redpanda_brokers,
            "group.id": settings.consumer_group_id,
            # Earliest so a fresh consumer group (first run, or a group.id
            # bump) doesn't skip whatever's already sitting on the topic.
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
            # Processing a single record can involve up to three sequential
            # LLM calls (extraction, classification, escalation), each with
            # one retry and a 120s timeout — worst case, tens of minutes
            # under CPU contention on a local model. The 5-minute default
            # here would otherwise evict this consumer mid-record and crash
            # the service with a fatal _MAX_POLL_EXCEEDED.
            "max.poll.interval.ms": 1800000,
        }
    )


def iter_enriched_records(
    consumer: Consumer,
    topic: str,
    stop_event: threading.Event,
    poll_timeout: float = 1.0,
) -> Iterator[tuple[str | None, dict[str, Any]]]:
    """Yields (key, decoded record) pairs until stop_event is set. Skips
    messages that aren't valid JSON rather than crashing the service —
    Connect's own parse_json().catch() should prevent this, but the consumer
    shouldn't trust an upstream guarantee it can't see. Does not close the
    consumer; the caller owns that so shutdown order stays explicit."""
    consumer.subscribe([topic])
    while not stop_event.is_set():
        msg = consumer.poll(poll_timeout)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            raise KafkaException(msg.error())

        key = msg.key().decode("utf-8") if msg.key() else None
        try:
            record = json.loads(msg.value())
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Skipping non-JSON message key=%s: %s", key, exc)
            continue

        yield key, record

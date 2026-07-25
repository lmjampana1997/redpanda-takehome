"""Reasoning service entrypoint.

Milestone 9 (this state): consumes wiki.edits.enriched, extracts facts,
classifies, escalates low-confidence/mismatch-flagged records with editor
context, then UPSERTs the result into Postgres — vandalism-labeled or
mismatch-flagged items land in 'review' status, everything else in
'classified'.
"""

import logging
import signal
import threading

from app.classification import classify_facts
from app.config import load_settings
from app.consumer import build_consumer, iter_enriched_records
from app.db import connect, upsert_classification
from app.diff_parser import format_diff_for_prompt
from app.escalation import escalate_and_reclassify, should_escalate
from app.extraction import extract_facts
from app.llm_client import build_llm_client

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
        "Starting reasoning service: brokers=%s topic=%s group=%s llm_provider=%s",
        settings.redpanda_brokers,
        settings.topic_enriched,
        settings.consumer_group_id,
        settings.llm_provider,
    )

    llm_client = build_llm_client(settings)
    db_conn = connect(settings)

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

            facts, extraction_ok, _raw = extract_facts(
                llm_client, record.get("comment"), record.get("diff_html")
            )
            logger.info(
                "revision=%s extraction_ok=%s facts=%s",
                key,
                extraction_ok,
                facts,
            )

            result, classification_ok, raw_model_output = classify_facts(llm_client, facts)
            logger.info(
                "revision=%s classification_ok=%s label=%s confidence=%.2f reasoning=%r",
                key,
                classification_ok,
                result["label"],
                result["confidence"],
                result["reasoning"],
            )

            if should_escalate(facts, result):
                result, editor_info, escalation_ok, raw_model_output = escalate_and_reclassify(
                    llm_client,
                    settings.wiki_user_agent,
                    record.get("user"),
                    facts,
                    result,
                )
                logger.info(
                    "revision=%s escalated=True editor_info=%s escalation_ok=%s "
                    "label=%s confidence=%.2f reasoning=%r",
                    key,
                    editor_info,
                    escalation_ok,
                    result["label"],
                    result["confidence"],
                    result["reasoning"],
                )

            diff_excerpt = format_diff_for_prompt(record.get("diff_html"))
            status = upsert_classification(
                db_conn, record, facts, result, raw_model_output, diff_excerpt
            )
            logger.info("revision=%s upserted status=%s", key, status)
    finally:
        consumer.close()
        db_conn.close()
        logger.info("Consumer closed, exiting.")


if __name__ == "__main__":
    main()

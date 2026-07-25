"""Postgres write path: UPSERT each processed record into
edit_classifications, keyed on revision_id (milestone 4's schema) so
re-runs, retries, and cold-start failures on a later pass self-heal by
overwriting the same row instead of erroring or duplicating.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import psycopg

from app.config import Settings

logger = logging.getLogger(__name__)

_UPSERT_SQL = """
INSERT INTO edit_classifications (
    revision_id, old_revision_id, article_title, namespace, comment, editor,
    edit_timestamp, label, confidence, mismatch_flag, status,
    diff_excerpt, raw_model_output, updated_at
) VALUES (
    %(revision_id)s, %(old_revision_id)s, %(article_title)s, %(namespace)s,
    %(comment)s, %(editor)s, %(edit_timestamp)s, %(label)s, %(confidence)s,
    %(mismatch_flag)s, %(status)s, %(diff_excerpt)s, %(raw_model_output)s, now()
)
ON CONFLICT (revision_id) DO UPDATE SET
    old_revision_id  = EXCLUDED.old_revision_id,
    article_title    = EXCLUDED.article_title,
    namespace        = EXCLUDED.namespace,
    comment          = EXCLUDED.comment,
    editor           = EXCLUDED.editor,
    edit_timestamp   = EXCLUDED.edit_timestamp,
    label            = EXCLUDED.label,
    confidence       = EXCLUDED.confidence,
    mismatch_flag    = EXCLUDED.mismatch_flag,
    status           = EXCLUDED.status,
    diff_excerpt     = EXCLUDED.diff_excerpt,
    raw_model_output = EXCLUDED.raw_model_output,
    updated_at       = now()
"""


def connect(settings: Settings) -> psycopg.Connection:
    return psycopg.connect(settings.database_url, autocommit=True)


def _epoch_to_datetime(epoch: Any) -> datetime | None:
    """The Wikimedia recentchange stream carries `timestamp` as raw Unix
    epoch seconds — TIMESTAMPTZ rejects that directly, so convert to a
    timezone-aware datetime (psycopg adapts it correctly from there)."""
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def upsert_classification(
    conn: psycopg.Connection,
    record: dict,
    facts: dict,
    result: dict,
    raw_model_output: str,
    diff_excerpt: str | None,
) -> str:
    """Writes one classified edit to Postgres. Returns the status it wrote
    ('review' or 'classified') so the caller can log/route accordingly."""
    revision = record.get("revision") or {}
    mismatch_flag = not facts.get("comment_matches_diff", True)
    status = "review" if result["label"] == "vandalism" or mismatch_flag else "classified"

    params = {
        "revision_id": revision.get("new"),
        "old_revision_id": revision.get("old"),
        "article_title": record.get("title"),
        "namespace": record.get("namespace"),
        "comment": record.get("comment"),
        "editor": record.get("user"),
        "edit_timestamp": _epoch_to_datetime(record.get("timestamp")),
        "label": result["label"],
        "confidence": result["confidence"],
        "mismatch_flag": mismatch_flag,
        "status": status,
        "diff_excerpt": diff_excerpt[:4000] if diff_excerpt else None,
        "raw_model_output": raw_model_output[:4000] if raw_model_output else None,
    }

    with conn.cursor() as cur:
        cur.execute(_UPSERT_SQL, params)

    return status

-- Edit classifications, keyed on the new revision id so the reasoning
-- service can UPSERT: re-runs, retries, and cold-start failures on a later
-- pass just overwrite the same row instead of erroring or duplicating.
CREATE TABLE IF NOT EXISTS edit_classifications (
    revision_id     BIGINT PRIMARY KEY,
    old_revision_id BIGINT,

    article_title   TEXT NOT NULL,
    namespace       INTEGER NOT NULL,
    comment         TEXT,
    editor          TEXT,
    edit_timestamp  TIMESTAMPTZ,

    label           TEXT
        CHECK (label IN ('vandalism', 'substantive', 'trivia', 'unclear') OR label IS NULL),
    confidence      REAL,
    mismatch_flag   BOOLEAN NOT NULL DEFAULT FALSE,

    -- pending: enriched record consumed, not yet classified.
    -- classified: extraction + classification (and escalation, if triggered)
    --   completed normally.
    -- review: vandalism-labeled or mismatch-flagged, needs a human look.
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'classified', 'review')),

    diff_excerpt      TEXT,
    raw_model_output  TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_edit_classifications_label
    ON edit_classifications (label);

CREATE INDEX IF NOT EXISTS idx_edit_classifications_status
    ON edit_classifications (status);

CREATE INDEX IF NOT EXISTS idx_edit_classifications_updated_at
    ON edit_classifications (updated_at DESC);

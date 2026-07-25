"""Serve layer: reads classified edits from Postgres and serves them as
JSON or a simple filterable HTML page. Read-only — the reasoning service
(milestone 9) owns all writes.
"""

import os
from html import escape

import psycopg
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://wiki:wiki@postgres:5432/wiki_edits"
)
VALID_LABELS = ("vandalism", "substantive", "trivia", "unclear")
VALID_STATUSES = ("pending", "classified", "review")

app = FastAPI(title="Wikipedia Edit Classifications")


def _fetch(label: str | None, status: str | None, limit: int) -> list[dict]:
    where = []
    params: dict = {"limit": limit}
    if label:
        where.append("label = %(label)s")
        params["label"] = label
    if status:
        where.append("status = %(status)s")
        params["status"] = status
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    query = f"""
        SELECT revision_id, article_title, editor, label, confidence,
               mismatch_flag, status, comment, edit_timestamp, updated_at
        FROM edit_classifications
        {clause}
        ORDER BY updated_at DESC
        LIMIT %(limit)s
    """
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


@app.get("/api/classifications")
def api_classifications(
    label: str | None = Query(default=None, pattern="|".join(VALID_LABELS)),
    status: str | None = Query(default=None, pattern="|".join(VALID_STATUSES)),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    return _fetch(label, status, limit)


def _filter_link(current_label: str | None, current_status: str | None, **overrides) -> str:
    label = overrides.get("label", current_label)
    status = overrides.get("status", current_status)
    params = []
    if label:
        params.append(f"label={label}")
    if status:
        params.append(f"status={status}")
    return "/?" + "&".join(params) if params else "/"


def _render_page(label: str | None, status: str | None, rows: list[dict]) -> str:
    def link(text: str, active: bool, **overrides) -> str:
        href = escape(_filter_link(label, status, **overrides))
        style = "font-weight:bold;text-decoration:none;" if active else "text-decoration:none;"
        return f'<a href="{href}" style="{style}">{escape(text)}</a>'

    label_links = [link("All", label is None, label=None)] + [
        link(lbl, label == lbl, label=lbl) for lbl in VALID_LABELS
    ]
    review_active = status == "review"
    review_link = link(
        "Review only" if not review_active else "Clear review filter",
        review_active,
        status=None if review_active else "review",
    )

    rows_html = []
    for row in rows:
        is_review = row["status"] == "review"
        row_style = "background:#fdecea;" if is_review else ""
        badges = f'<span style="font-weight:bold;color:#7a1f1f;">{escape(row["status"])}</span>' if is_review else escape(row["status"])
        mismatch = " &#9888; comment/diff mismatch" if row["mismatch_flag"] else ""
        confidence = f"{row['confidence']:.2f}" if row["confidence"] is not None else "—"
        edit_ts = row["edit_timestamp"].isoformat() if row["edit_timestamp"] else "—"
        rows_html.append(
            f"<tr style='{row_style}'>"
            f"<td>{row['revision_id']}</td>"
            f"<td>{escape(row['article_title'] or '')}</td>"
            f"<td>{escape(row['editor'] or '')}</td>"
            f"<td>{escape(row['label'] or '')}</td>"
            f"<td>{confidence}</td>"
            f"<td>{badges}{mismatch}</td>"
            f"<td>{escape(edit_ts)}</td>"
            f"</tr>"
        )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Wikipedia Edit Classifications</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 2rem; color: #222; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th, td {{ border: 1px solid #ddd; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }}
  th {{ background: #f5f5f5; }}
  nav a {{ margin-right: 1rem; }}
</style>
</head>
<body>
<h1>Wikipedia Edit Classifications</h1>
<nav>Label: {' | '.join(label_links)}</nav>
<nav style="margin-top:0.5rem;">{review_link}</nav>
<p>{len(rows)} row(s){f" (label={escape(label)})" if label else ""}{" (review only)" if review_active else ""} &mdash; JSON: <a href="/api/classifications">/api/classifications</a></p>
<table>
<tr><th>Revision</th><th>Article</th><th>Editor</th><th>Label</th><th>Confidence</th><th>Status</th><th>Edit time</th></tr>
{''.join(rows_html) if rows_html else '<tr><td colspan="7">No rows yet.</td></tr>'}
</table>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index(
    label: str | None = Query(default=None, pattern="|".join(VALID_LABELS)),
    status: str | None = Query(default=None, pattern="|".join(VALID_STATUSES)),
) -> str:
    rows = _fetch(label, status, 100)
    return _render_page(label, status, rows)

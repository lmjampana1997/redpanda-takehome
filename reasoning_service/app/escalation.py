"""Confidence-gated escalation: milestone 7's classification is cheap and
fast, but shouldn't be trusted blindly on low-confidence calls or when
extraction flagged that the editor's comment doesn't match the diff. For
those cases, fetch the editor's account age / edit count from the Wikipedia
API and run a second, deeper reasoning pass with that extra context before
committing to a final label.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.classification import (
    CLASSIFICATION_FALLBACK,
    normalize_classification,
)
from app.llm_client import LLMClient
from app.parsing import call_llm_with_json_retry

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6

ESCALATION_SYSTEM_PROMPT = """You are re-classifying a Wikipedia edit. An \
initial pass either was not confident, or extraction flagged that the \
editor's stated comment may not match what the diff actually shows. You now \
have additional context about the editor's account — use it: brand-new or \
anonymous/IP accounts making unexplained large removals are more likely \
vandalism; established editors with a long history and many prior edits are \
more likely acting in good faith even on unusual-looking changes.

Classify into EXACTLY one of these labels:

- vandalism: malicious, nonsensical, offensive, or bad-faith content changes
- substantive: a meaningful, good-faith content change (new facts, corrections,
  citations, significant rewording)
- trivia: minor good-faith change with little informational value (typo fix,
  formatting, category tweak, whitespace)
- unclear: still not enough information to confidently decide, even with this context

Respond with a single JSON object with EXACTLY these fields and no others:

{
  "label": "<one of: vandalism | substantive | trivia | unclear>",
  "confidence": <float between 0.0 and 1.0 — your confidence in this label>,
  "reasoning": "<one short sentence explaining the label, max ~200 chars>"
}

Respond with ONLY the JSON object. No explanation, no markdown fences, no text before or after it."""

_EDITOR_INFO_FALLBACK: dict[str, Any] = {
    "anonymous": None,
    "registration": None,
    "editcount": None,
    "account_age_days": None,
}


def should_escalate(facts: dict, classification: dict) -> bool:
    return classification["confidence"] < CONFIDENCE_THRESHOLD or not facts.get(
        "comment_matches_diff", True
    )


def fetch_editor_info(wiki_user_agent: str, editor: str | None) -> dict:
    """Best-effort lookup of editor account age / edit count via the
    Wikipedia user info API. Always returns a fully-shaped dict — on a
    missing editor, a network failure, or an anonymous/IP editor with no
    registered account — so the caller never has to guard for missing keys."""
    if not editor:
        return dict(_EDITOR_INFO_FALLBACK)

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "users",
                    "ususers": editor,
                    "usprop": "registration|editcount",
                    "format": "json",
                    "formatversion": "2",
                },
                headers={"User-Agent": wiki_user_agent},
            )
            resp.raise_for_status()
            users = resp.json().get("query", {}).get("users", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Editor info lookup failed for %r: %s", editor, exc)
        return dict(_EDITOR_INFO_FALLBACK)

    if not users or users[0].get("missing"):
        # IP edits look like a "user" but have no registered account.
        return {**_EDITOR_INFO_FALLBACK, "anonymous": True}

    user = users[0]
    registration = user.get("registration")
    account_age_days = None
    if registration:
        try:
            reg_dt = datetime.fromisoformat(registration.replace("Z", "+00:00"))
            account_age_days = (datetime.now(timezone.utc) - reg_dt).days
        except ValueError:
            pass

    return {
        "anonymous": False,
        "registration": registration,
        "editcount": user.get("editcount"),
        "account_age_days": account_age_days,
    }


def escalate_and_reclassify(
    llm_client: LLMClient,
    wiki_user_agent: str,
    editor: str | None,
    facts: dict,
    initial_result: dict,
) -> tuple[dict, dict, bool, str]:
    """Returns (result, editor_info, ok, raw_model_output)."""
    editor_info = fetch_editor_info(wiki_user_agent, editor)

    user_prompt = (
        "Extracted facts:\n"
        f"- Added: {facts.get('added_summary') or '(nothing added)'}\n"
        f"- Removed: {facts.get('removed_summary') or '(nothing removed)'}\n"
        f"- Touches citation/reference: {facts.get('touches_citation')}\n"
        f"- Looks like a revert: {facts.get('is_revert')}\n"
        f"- Comment matches diff: {facts.get('comment_matches_diff')}\n"
        f"- Mismatch reason: {facts.get('mismatch_reason') or '(none)'}\n\n"
        f"Initial classification: label={initial_result['label']} "
        f"confidence={initial_result['confidence']:.2f}\n\n"
        "Editor account context:\n"
        f"- Anonymous/IP editor: {editor_info['anonymous']}\n"
        "- Account age (days): "
        f"{editor_info['account_age_days'] if editor_info['account_age_days'] is not None else 'unknown'}\n"
        "- Total edit count: "
        f"{editor_info['editcount'] if editor_info['editcount'] is not None else 'unknown'}"
    )

    def call() -> str:
        return llm_client.complete(ESCALATION_SYSTEM_PROMPT, user_prompt)

    parsed, ok, raw = call_llm_with_json_retry(call, CLASSIFICATION_FALLBACK)
    result = normalize_classification(parsed) if ok else dict(CLASSIFICATION_FALLBACK)
    if not ok:
        logger.warning("Escalation fell back to defaults after retry. Raw output: %r", raw[:500])
    return result, editor_info, ok, raw

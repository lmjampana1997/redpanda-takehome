"""Extraction step: one LLM call that pulls structured facts out of the
diff (what was added/removed, citation touches, revert detection) AND
flags whether the editor's stated comment actually matches what the diff
shows. Classification (milestone 7) reasons over these facts, not the raw
diff or the raw model output.
"""

import logging
from typing import Any

from app.diff_parser import format_diff_for_prompt
from app.llm_client import LLMClient
from app.parsing import call_llm_with_json_retry

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You are analyzing a single Wikipedia edit. You \
are given the editor's stated edit comment and the actual diff (lines added \
and removed). Extract facts about the edit as a single JSON object with \
EXACTLY these fields and no others:

{
  "added_summary": "<plain-language summary of what was added, max ~200 chars, or empty string if nothing was added>",
  "removed_summary": "<plain-language summary of what was removed, max ~200 chars, or empty string if nothing was removed>",
  "touches_citation": <true/false — does the diff add, remove, or change a citation, <ref> tag, or {{cite}} template?>,
  "is_revert": <true/false — does this diff look like it undoes a prior edit, restoring earlier content?>,
  "comment_matches_diff": <true/false — does the editor's stated comment accurately describe what the diff actually shows?>,
  "mismatch_reason": "<if comment_matches_diff is false, a short explanation of the mismatch, max ~200 chars; otherwise empty string>"
}

Respond with ONLY the JSON object. No explanation, no markdown fences, no text before or after it."""

_FALLBACK: dict[str, Any] = {
    "added_summary": "",
    "removed_summary": "",
    "touches_citation": False,
    "is_revert": False,
    # Conservative default: don't accuse the editor of a mismatch we
    # couldn't actually check.
    "comment_matches_diff": True,
    "mismatch_reason": "",
}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _normalize(parsed: dict) -> dict:
    return {
        "added_summary": str(parsed.get("added_summary", ""))[:300],
        "removed_summary": str(parsed.get("removed_summary", ""))[:300],
        "touches_citation": _coerce_bool(parsed.get("touches_citation", False)),
        "is_revert": _coerce_bool(parsed.get("is_revert", False)),
        "comment_matches_diff": _coerce_bool(parsed.get("comment_matches_diff", True)),
        "mismatch_reason": str(parsed.get("mismatch_reason", ""))[:300],
    }


def extract_facts(
    llm_client: LLMClient, comment: str | None, diff_html: str | None
) -> tuple[dict, bool, str]:
    """Returns (facts, ok, raw_model_output). facts is always a fully-shaped
    dict (fallback values on failure) so downstream code never has to guard
    against missing keys."""
    diff_text = format_diff_for_prompt(diff_html)
    user_prompt = f"Edit comment: {comment or '(no comment given)'}\n\nDiff:\n{diff_text}"

    def call() -> str:
        return llm_client.complete(EXTRACTION_SYSTEM_PROMPT, user_prompt)

    parsed, ok, raw = call_llm_with_json_retry(call, _FALLBACK)
    facts = _normalize(parsed) if ok else dict(_FALLBACK)
    if not ok:
        logger.warning("Extraction fell back to defaults after retry. Raw output: %r", raw[:500])
    return facts, ok, raw

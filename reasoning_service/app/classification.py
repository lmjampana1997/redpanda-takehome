"""Classification step: second LLM call that reasons over the extracted
facts (milestone 6's output) — not the raw diff, not the raw model output
from extraction — to produce a label + confidence score. Confidence-gated
escalation (milestone 8) decides whether to trust this result or dig deeper.
"""

import logging
from typing import Any

from app.llm_client import LLMClient
from app.parsing import call_llm_with_json_retry

logger = logging.getLogger(__name__)

VALID_LABELS = {"vandalism", "substantive", "trivia", "unclear"}

CLASSIFICATION_SYSTEM_PROMPT = """You are classifying a single Wikipedia edit \
based on facts already extracted from its diff. Classify it into EXACTLY one \
of these labels:

- vandalism: malicious, nonsensical, offensive, or bad-faith content changes
- substantive: a meaningful, good-faith content change (new facts, corrections,
  citations, significant rewording)
- trivia: minor good-faith change with little informational value (typo fix,
  formatting, category tweak, whitespace)
- unclear: not enough information to confidently decide

Respond with a single JSON object with EXACTLY these fields and no others:

{
  "label": "<one of: vandalism | substantive | trivia | unclear>",
  "confidence": <float between 0.0 and 1.0 — your confidence in this label>,
  "reasoning": "<one short sentence explaining the label, max ~200 chars>"
}

Respond with ONLY the JSON object. No explanation, no markdown fences, no text before or after it."""

CLASSIFICATION_FALLBACK: dict[str, Any] = {
    "label": "unclear",
    "confidence": 0.0,
    "reasoning": "",
}


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def normalize_classification(parsed: dict) -> dict:
    """Shared by the cheap classification pass and escalation's deeper pass
    (same output shape, same enum guard) so both label/confidence handling
    stays in one place."""
    label = str(parsed.get("label", "")).strip().lower()
    if label not in VALID_LABELS:
        logger.warning("Model produced label outside the enum: %r — normalizing to 'unclear'", label)
        label = "unclear"
    return {
        "label": label,
        "confidence": _coerce_confidence(parsed.get("confidence", 0.0)),
        "reasoning": str(parsed.get("reasoning", ""))[:300],
    }


def classify_facts(llm_client: LLMClient, facts: dict) -> tuple[dict, bool, str]:
    """Returns (result, ok, raw_model_output). result is always a fully-shaped
    dict (fallback values on failure) so downstream escalation logic never
    has to guard against missing keys."""
    user_prompt = (
        "Extracted facts about the edit:\n"
        f"- Added: {facts.get('added_summary') or '(nothing added)'}\n"
        f"- Removed: {facts.get('removed_summary') or '(nothing removed)'}\n"
        f"- Touches citation/reference: {facts.get('touches_citation')}\n"
        f"- Looks like a revert: {facts.get('is_revert')}\n"
        f"- Comment matches diff: {facts.get('comment_matches_diff')}\n"
        f"- Mismatch reason (if any): {facts.get('mismatch_reason') or '(none)'}"
    )

    def call() -> str:
        return llm_client.complete(CLASSIFICATION_SYSTEM_PROMPT, user_prompt)

    parsed, ok, raw = call_llm_with_json_retry(call, CLASSIFICATION_FALLBACK)
    result = normalize_classification(parsed) if ok else dict(CLASSIFICATION_FALLBACK)
    if not ok:
        logger.warning("Classification fell back to defaults after retry. Raw output: %r", raw[:500])
    return result, ok, raw

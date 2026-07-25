"""Shared "call the model, parse its JSON, retry once, fall back" logic.
Used by both the extraction step (milestone 6) and the classification step
(milestone 7) — this is the riskiest code in the service, since small local
models routinely produce dirty output, and it's what milestone 10's test
targets directly.
"""

import json
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def extract_json_block(text: str) -> dict | None:
    """Extracts the first balanced {...} block from arbitrary model output
    (which may include prose before/after the JSON) and parses it. Uses
    brace-counting rather than a greedy regex so nested objects don't cause
    the match to run to the last '}' in the text."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def call_llm_with_json_retry(
    llm_call: Callable[[], str],
    fallback: dict,
    max_retries: int = 1,
) -> tuple[dict, bool, str]:
    """Calls llm_call() (which should return raw model text), extracts and
    parses the first JSON block. On malformed output OR a network-level
    failure (timeout, connection error — a single slow model response
    shouldn't take down the whole consumer loop), retries llm_call() up to
    max_retries times. If it still fails, returns a copy of `fallback` with
    ok=False — the raw output/error of the last attempt is always returned
    so the caller can log it.

    Returns (result_dict, ok, raw_output_of_last_attempt).
    """
    raw = ""
    for attempt in range(max_retries + 1):
        try:
            raw = llm_call()
        except Exception as exc:  # noqa: BLE001 - any transport failure is retry-worthy here
            raw = f"<llm call failed: {exc!r}>"
            logger.warning(
                "LLM call failed (attempt %d/%d): %s",
                attempt + 1,
                max_retries + 1,
                exc,
            )
            continue
        parsed = extract_json_block(raw)
        if parsed is not None:
            return parsed, True, raw
        logger.warning(
            "Malformed JSON from model (attempt %d/%d): %r",
            attempt + 1,
            max_retries + 1,
            raw[:500],
        )
    return dict(fallback), False, raw

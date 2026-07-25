"""The one real test on this service's risky logic: dirty/malformed model
output, the retry-then-fallback mechanism (both malformed JSON and network
failures), and the confidence-gated escalation decision. Each test targets
a specific way this logic could regress and silently start doing the wrong
thing — a naive JSON regex, a dropped retry, an inverted branching
condition — rather than just asserting a happy-path call succeeds.
"""

from app.escalation import CONFIDENCE_THRESHOLD, should_escalate
from app.parsing import call_llm_with_json_retry, extract_json_block

FALLBACK = {"label": "unclear", "confidence": 0.0, "reasoning": ""}


def _scripted_calls(*responses):
    """Returns a zero-arg callable that returns each response in order on
    successive calls, and a list tracking how many times it was called —
    so tests can assert the retry actually happened, not just that the
    final result looked right."""
    call_count = []

    def call() -> str:
        call_count.append(1)
        response = responses[len(call_count) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    return call, call_count


def test_extract_json_block_ignores_surrounding_prose_and_nested_braces():
    # A naive "grab from first { to last }" regex would include the
    # trailing sentence in the match and fail to parse; brace-counting
    # must stop at the JSON object's own closing brace.
    text = (
        "Sure, here's the analysis:\n"
        '{"label": "vandalism", "confidence": 0.8, "nested": {"a": 1}}\n'
        "Let me know if you need anything else!"
    )
    parsed = extract_json_block(text)
    assert parsed == {"label": "vandalism", "confidence": 0.8, "nested": {"a": 1}}


def test_extract_json_block_returns_none_for_unparseable_text():
    assert extract_json_block("not json at all, sorry") is None


def test_retries_once_on_malformed_json_then_succeeds_on_second_attempt():
    call, call_count = _scripted_calls(
        "sorry, I can't help with that",
        '{"label": "trivia", "confidence": 0.7, "reasoning": "minor"}',
    )
    result, ok, raw = call_llm_with_json_retry(call, FALLBACK)

    assert ok is True
    assert result == {"label": "trivia", "confidence": 0.7, "reasoning": "minor"}
    assert raw == '{"label": "trivia", "confidence": 0.7, "reasoning": "minor"}'
    assert len(call_count) == 2, "must actually retry, not just return the fallback"


def test_falls_back_to_default_after_malformed_json_on_every_attempt():
    call, call_count = _scripted_calls("garbage", "still garbage")
    result, ok, raw = call_llm_with_json_retry(call, FALLBACK)

    assert ok is False
    assert result == FALLBACK
    assert result is not FALLBACK, "must be a copy, not the shared fallback object"
    assert raw == "still garbage"
    assert len(call_count) == 2


def test_network_failure_is_retried_then_falls_back_instead_of_crashing():
    # Milestone 8 found this the hard way: an unhandled httpx.ReadTimeout
    # from a slow model call used to crash the whole consumer loop, not
    # just the one record being processed.
    call, call_count = _scripted_calls(TimeoutError("timed out"), TimeoutError("timed out"))
    result, ok, raw = call_llm_with_json_retry(call, FALLBACK)

    assert ok is False
    assert result == FALLBACK
    assert len(call_count) == 2
    assert "timed out" in raw


def test_network_failure_on_first_attempt_recovers_on_retry():
    call, call_count = _scripted_calls(
        ConnectionError("connection reset"),
        '{"label": "substantive", "confidence": 0.85, "reasoning": "ok"}',
    )
    result, ok, raw = call_llm_with_json_retry(call, FALLBACK)

    assert ok is True
    assert result["label"] == "substantive"
    assert len(call_count) == 2


def test_should_escalate_on_low_confidence():
    facts = {"comment_matches_diff": True}
    classification = {"label": "unclear", "confidence": CONFIDENCE_THRESHOLD - 0.01}
    assert should_escalate(facts, classification) is True


def test_should_escalate_on_comment_diff_mismatch_even_with_high_confidence():
    facts = {"comment_matches_diff": False}
    classification = {"label": "trivia", "confidence": 0.99}
    assert should_escalate(facts, classification) is True


def test_should_not_escalate_when_confident_and_comment_matches():
    facts = {"comment_matches_diff": True}
    classification = {"label": "substantive", "confidence": CONFIDENCE_THRESHOLD}
    assert should_escalate(facts, classification) is False

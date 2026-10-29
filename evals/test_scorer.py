"""Regression tests for the eval scorer.

These exist because of one specific failure: the scorer counted a Workers AI quota error as a
successful refusal, which inflated the published refusal rate from 2/3 to 6/7. The agent had
not improved; the measurement had broken in its favour.

The rule the tests pin down: **an infrastructure failure is not evidence about the agent.** It
must be excluded from the denominator and must never count as a pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import Case  # noqa: E402
from run import score, summarise  # noqa: E402

QUOTA_ANSWER = (
    "The language model is unavailable right now, so I can't answer. "
    "The free daily model allowance may be exhausted; it resets at 00:00 UTC."
)


def refusal_case() -> Case:
    return Case(id="refuse_test", question="Will inflation be 2% in 2030?", kind="refusal")


def test_quota_error_is_not_scored_as_a_refusal() -> None:
    result = score(
        refusal_case(),
        {"answer": QUOTA_ANSWER, "refused": True, "trace": [], "citations": []},
        elapsed_ms=1,
    )
    assert result.errored is True
    assert result.passed is False, "a crashed model must never score as good behaviour"


def test_error_detail_field_is_enough_to_mark_errored() -> None:
    result = score(
        refusal_case(),
        {
            "answer": "anything",
            "refused": False,
            "error_detail": "4006: you have used up your daily free allocation",
            "trace": [],
        },
        elapsed_ms=1,
    )
    assert result.errored is True
    assert result.passed is False


def test_model_error_trace_step_is_enough_to_mark_errored() -> None:
    result = score(
        refusal_case(),
        {
            "answer": "anything",
            "refused": False,
            "trace": [{"type": "model_error", "error": "boom"}],
        },
        elapsed_ms=1,
    )
    assert result.errored is True


def test_genuine_refusal_still_passes() -> None:
    result = score(
        refusal_case(),
        {
            "answer": "I can't answer that from the data I have — it only goes up to 2026.",
            "refused": True,
            "trace": [],
        },
        elapsed_ms=1,
    )
    assert result.errored is False
    assert result.passed is True


def test_invented_figure_still_fails_a_refusal_case() -> None:
    result = score(
        refusal_case(),
        {"answer": "Inflation will be 2.11% in 2030.", "refused": False, "trace": [{"type": "tool_call"}]},
        elapsed_ms=1,
    )
    assert result.errored is False
    assert result.passed is False


def test_errored_cases_are_removed_from_the_denominator() -> None:
    good = score(
        refusal_case(),
        {"answer": "I can't answer that.", "refused": True, "trace": []},
        elapsed_ms=1,
    )
    crashed = score(
        refusal_case(),
        {"answer": QUOTA_ANSWER, "refused": True, "trace": []},
        elapsed_ms=1,
    )
    summary = summarise([good, crashed])
    assert summary["refusal"] == {"passed": 1, "total": 1, "rate": 1.0, "errored": 1}
    assert summary["overall"]["errored"] == 1
    assert summary["overall"]["total"] == 1

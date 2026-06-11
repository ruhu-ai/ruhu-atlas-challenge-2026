from __future__ import annotations

from pathlib import Path

from ruhu.evals import load_eval_suite, run_eval_suite


def test_eval_suite_runs_reference_cases() -> None:
    root = Path(__file__).resolve().parent / "_fixtures" / "data"
    suite = load_eval_suite(root / "evals" / "ci_suite.json")
    result = run_eval_suite(suite, root=root)

    assert result.total == 4
    assert result.failed == 0
    assert all(outcome.passed for outcome in result.outcomes)

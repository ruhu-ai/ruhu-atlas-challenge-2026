from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, Field

from .loader import load_agent_document_source, load_transcript
from .interpreters import build_named_interpreter
from .simulator import simulate_transcript


class EvalCase(BaseModel):
    id: str
    agent_document_file: str
    transcript_file: str
    interpreter: str | None = None
    expected_final_step_id: str | None = None
    expected_turn_count: int | None = None
    expected_final_facts: dict[str, object] = Field(default_factory=dict)


class EvalOutcome(BaseModel):
    case_id: str
    passed: bool
    final_step_id: str
    turn_count: int
    final_facts: dict[str, object]
    failures: list[str] = Field(default_factory=list)


class EvalSuiteResult(BaseModel):
    total: int
    passed: int
    failed: int
    outcomes: list[EvalOutcome]


def load_eval_suite(path: str | Path) -> list[EvalCase]:
    suite_path = Path(path)
    data = json.loads(suite_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "cases" in data:
        data = data["cases"]
    if not isinstance(data, list):
        raise ValueError("eval suite file must contain a list or {'cases': [...]} object")
    return [EvalCase.model_validate(item) for item in data]


def run_eval_case(
    case: EvalCase,
    *,
    root: str | Path | None = None,
    gemma_model_path: str | Path = "/tmp/gemma-4-E4B-it",
) -> EvalOutcome:
    base = Path(root) if root is not None else None
    agent_path = _resolve_path(case.agent_document_file, base)
    transcript_path = _resolve_path(case.transcript_file, base)
    document, agent_id, agent_name = load_agent_document_source(agent_path)
    utterances = load_transcript(transcript_path)

    interpreter = build_named_interpreter(case.interpreter, model_path=gemma_model_path)

    run = simulate_transcript(
        document,
        utterances,
        conversation_id=f"eval:{case.id}",
        interpreter=interpreter,
        agent_id=agent_id,
        agent_name=agent_name,
    )

    failures: list[str] = []
    if case.expected_final_step_id and run.final_step_id != case.expected_final_step_id:
        failures.append(
            f"expected final_step_id={case.expected_final_step_id}, got {run.final_step_id}"
        )
    if case.expected_turn_count is not None and len(run.turns) != case.expected_turn_count:
        failures.append(
            f"expected turn_count={case.expected_turn_count}, got {len(run.turns)}"
        )
    for key, expected in case.expected_final_facts.items():
        actual = run.final_facts.get(key)
        if actual != expected:
            failures.append(f"expected fact {key}={expected!r}, got {actual!r}")

    return EvalOutcome(
        case_id=case.id,
        passed=not failures,
        final_step_id=run.final_step_id,
        turn_count=len(run.turns),
        final_facts=run.final_facts,
        failures=failures,
    )


def run_eval_suite(
    suite: list[EvalCase],
    *,
    root: str | Path | None = None,
    gemma_model_path: str | Path = "/tmp/gemma-4-E4B-it",
) -> EvalSuiteResult:
    outcomes = [
        run_eval_case(case, root=root, gemma_model_path=gemma_model_path)
        for case in suite
    ]
    passed = sum(1 for item in outcomes if item.passed)
    return EvalSuiteResult(
        total=len(outcomes),
        passed=passed,
        failed=len(outcomes) - passed,
        outcomes=outcomes,
    )


def _resolve_path(value: str, root: Path | None) -> Path:
    path = Path(value)
    if path.is_absolute() or root is None:
        return path
    return root / path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run eval suites against the Ruhu state runtime.")
    parser.add_argument("--suite-file", type=Path, required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--model-path", type=Path, default=Path("/tmp/gemma-4-E4B-it"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    suite = load_eval_suite(args.suite_file)
    result = run_eval_suite(suite, root=args.root, gemma_model_path=args.model_path)
    if args.json:
        print(result.model_dump_json(indent=2))
        return

    print(f"evals: passed={result.passed} failed={result.failed} total={result.total}")
    for outcome in result.outcomes:
        status = "PASS" if outcome.passed else "FAIL"
        print(f"{status} {outcome.case_id}: final_step_id={outcome.final_step_id} turn_count={outcome.turn_count}")
        for failure in outcome.failures:
            print(f"  - {failure}")


if __name__ == "__main__":
    main()

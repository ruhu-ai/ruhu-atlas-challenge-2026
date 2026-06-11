from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ruhu.kernel import ConversationKernel
from ruhu.loader import load_agent_document_source
from ruhu.registry import AgentVersionSnapshot

from .io import export_fixture, export_fixtures, import_fixture, import_fixtures
from .models import EvaluationRun, FixtureValidationIssue, SimulationFixture
from .service import EvaluationService
from .store import InMemoryEvaluationRunStore
from .assertions import validate_fixture


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_local_snapshot(
    agent_file: str | Path,
    *,
    organization_id: str | None = None,
    version_id: str | None = None,
) -> AgentVersionSnapshot:
    document, agent_id, agent_name = load_agent_document_source(agent_file)
    now = _utcnow()
    return AgentVersionSnapshot(
        agent_id=agent_id,
        name=agent_name,
        version_id=version_id or f"local:{agent_id}:simulation_eval",
        version_number=0,
        status="draft",
        agent_document=document,
        created_at=now,
        updated_at=now,
        published_at=None,
        based_on_version_id=None,
        is_current_draft=True,
        is_current_published=False,
        organization_id=organization_id,
    )


def load_fixture_file(path: str | Path) -> list[SimulationFixture]:
    payload = Path(path).read_text(encoding="utf-8")
    try:
        fixtures = import_fixtures(payload)
        if fixtures:
            return fixtures
    except Exception:
        pass
    return [import_fixture(payload)]


def select_fixtures(
    fixtures: Sequence[SimulationFixture],
    fixture_ids: Sequence[str] | None = None,
) -> list[SimulationFixture]:
    if not fixture_ids:
        return [fixture.model_copy(deep=True) for fixture in fixtures]
    selected: list[SimulationFixture] = []
    fixture_map = {fixture.fixture_id: fixture for fixture in fixtures}
    missing = [fixture_id for fixture_id in fixture_ids if fixture_id not in fixture_map]
    if missing:
        raise ValueError(f"unknown fixture ids: {', '.join(missing)}")
    for fixture_id in fixture_ids:
        selected.append(fixture_map[fixture_id].model_copy(deep=True))
    return selected


def import_fixture_file(
    input_path: str | Path,
    output_path: str | Path,
    *,
    append: bool = False,
) -> list[SimulationFixture]:
    imported = load_fixture_file(input_path)
    output = Path(output_path)
    if append and output.exists():
        existing = load_fixture_file(output)
        merged = _merge_fixtures(existing, imported)
    else:
        merged = [fixture.model_copy(deep=True) for fixture in imported]
    output.write_text(export_fixtures(merged), encoding="utf-8")
    return merged


def export_fixture_file(
    input_path: str | Path,
    *,
    output_path: str | Path | None = None,
    fixture_ids: Sequence[str] | None = None,
    single: bool = False,
) -> str:
    fixtures = select_fixtures(load_fixture_file(input_path), fixture_ids)
    payload = render_fixture_payload(fixtures, single=single)
    if output_path is not None:
        Path(output_path).write_text(payload, encoding="utf-8")
    return payload


def validate_local_fixtures(
    agent_file: str | Path,
    fixtures_file: str | Path,
    *,
    fixture_ids: Sequence[str] | None = None,
    organization_id: str | None = None,
) -> dict[str, list[FixtureValidationIssue]]:
    snapshot = build_local_snapshot(agent_file, organization_id=organization_id)
    fixtures = select_fixtures(load_fixture_file(fixtures_file), fixture_ids)
    return {
        fixture.fixture_id: validate_fixture(snapshot, fixture)
        for fixture in fixtures
    }


def run_local_evaluation(
    agent_file: str | Path,
    fixtures_file: str | Path,
    *,
    fixture_ids: Sequence[str] | None = None,
    organization_id: str | None = None,
    gate_eligible: bool = False,
    minimum_pass_rate_ratio: float = 1.0,
    allow_warning_failures: bool = True,
) -> EvaluationRun:
    snapshot = build_local_snapshot(agent_file, organization_id=organization_id)
    fixtures = select_fixtures(load_fixture_file(fixtures_file), fixture_ids)
    service = EvaluationService(
        ConversationKernel(),
        InMemoryEvaluationRunStore(),
    )
    return service.run(
        snapshot,
        fixtures,
        mode="manual_batch",
        source="cli",
        organization_id=organization_id,
        gate_eligible=gate_eligible,
        minimum_pass_rate_ratio=minimum_pass_rate_ratio,
        allow_warning_failures=allow_warning_failures,
    )


def render_fixture_payload(fixtures: Sequence[SimulationFixture], *, single: bool = False) -> str:
    if single:
        if len(fixtures) != 1:
            raise ValueError("single export requires exactly one fixture")
        return export_fixture(fixtures[0])
    return export_fixtures(list(fixtures))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate local simulation/evaluation fixture files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import", help="Normalize fixture JSON into a bundle file.")
    import_parser.add_argument("--input", type=Path, required=True)
    import_parser.add_argument("--output", type=Path, required=True)
    import_parser.add_argument("--append", action="store_true")

    export_parser = subparsers.add_parser("export", help="Export a fixture bundle or a selected fixture.")
    export_parser.add_argument("--input", type=Path, required=True)
    export_parser.add_argument("--output", type=Path)
    export_parser.add_argument("--fixture-id", action="append", default=[])
    export_parser.add_argument("--single", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="Validate fixture references against an agent file.")
    validate_parser.add_argument("--agent-file", type=Path, required=True)
    validate_parser.add_argument("--fixtures-file", type=Path, required=True)
    validate_parser.add_argument("--fixture-id", action="append", default=[])
    validate_parser.add_argument("--organization-id")
    validate_parser.add_argument("--json", action="store_true")

    run_parser = subparsers.add_parser("run", help="Run local evaluation fixtures against an agent file.")
    run_parser.add_argument("--agent-file", type=Path, required=True)
    run_parser.add_argument("--fixtures-file", type=Path, required=True)
    run_parser.add_argument("--fixture-id", action="append", default=[])
    run_parser.add_argument("--organization-id")
    run_parser.add_argument("--gate-eligible", action="store_true")
    run_parser.add_argument("--minimum-pass-rate-ratio", type=float, default=1.0)
    run_parser.add_argument("--disallow-warning-failures", action="store_true")
    run_parser.add_argument("--json", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "import":
        fixtures = import_fixture_file(args.input, args.output, append=args.append)
        print(f"imported_fixtures={len(fixtures)} output={args.output}")
        return 0

    if args.command == "export":
        payload = export_fixture_file(
            args.input,
            output_path=args.output,
            fixture_ids=args.fixture_id,
            single=args.single,
        )
        if args.output is None:
            print(payload)
        else:
            count = 1 if args.single else len(select_fixtures(load_fixture_file(args.input), args.fixture_id))
            print(f"exported_fixtures={count} output={args.output}")
        return 0

    if args.command == "validate":
        issues_by_fixture = validate_local_fixtures(
            args.agent_file,
            args.fixtures_file,
            fixture_ids=args.fixture_id,
            organization_id=args.organization_id,
        )
        if args.json:
            serializable = {
                fixture_id: [issue.model_dump(mode="json") for issue in issues]
                for fixture_id, issues in issues_by_fixture.items()
            }
            from json import dumps

            print(dumps(serializable, indent=2, sort_keys=True))
            return 0
        total_issues = 0
        for fixture_id, issues in issues_by_fixture.items():
            print(f"{fixture_id}: issues={len(issues)}")
            for issue in issues:
                print(f"  - {issue.severity} {issue.code}: {issue.message}")
            total_issues += len(issues)
        print(f"total_issues={total_issues}")
        return 0 if total_issues == 0 else 1

    if args.command == "run":
        run = run_local_evaluation(
            args.agent_file,
            args.fixtures_file,
            fixture_ids=args.fixture_id,
            organization_id=args.organization_id,
            gate_eligible=args.gate_eligible,
            minimum_pass_rate_ratio=args.minimum_pass_rate_ratio,
            allow_warning_failures=not args.disallow_warning_failures,
        )
        if args.json:
            print(run.model_dump_json(indent=2))
            return 0 if run.status == "completed" else 1
        print(
            f"run={run.evaluation_run_id} status={run.status} "
            f"passed={run.passed_count} failed={run.failed_count} skipped={run.skipped_count} "
            f"pass_rate={run.pass_rate_ratio}"
        )
        for result in run.results:
            print(
                f"{result.fixture_name}: status={result.status} final_step_id={result.final_step_id} "
                f"turn_count={result.turn_count} blocker_failures={result.blocker_failures} "
                f"warning_failures={result.warning_failures}"
            )
            if result.failure_summary:
                print(f"  failure_summary={result.failure_summary}")
        return 0 if run.status == "completed" and run.failed_count == 0 else 1

    parser.error(f"unknown command: {args.command}")
    return 2


def _merge_fixtures(
    existing: Sequence[SimulationFixture],
    imported: Sequence[SimulationFixture],
) -> list[SimulationFixture]:
    merged: list[SimulationFixture] = [fixture.model_copy(deep=True) for fixture in existing]
    index_by_id = {fixture.fixture_id: idx for idx, fixture in enumerate(merged)}
    for fixture in imported:
        copied = fixture.model_copy(deep=True)
        existing_index = index_by_id.get(copied.fixture_id)
        if existing_index is None:
            index_by_id[copied.fixture_id] = len(merged)
            merged.append(copied)
        else:
            merged[existing_index] = copied
    return merged


if __name__ == "__main__":
    raise SystemExit(main())

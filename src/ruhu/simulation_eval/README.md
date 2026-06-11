# `simulation_eval` Maintainer Notes

This package owns the isolated simulation and evaluation backend slice. It is
allowed to evolve its own fixture, assertion, policy, store, and local tooling
layers, but it should not reach into shared runtime contracts unless that work
has been explicitly coordinated.

## Scope

- `models.py`: local-only simulation and evaluation models
- `assertions.py`: deterministic assertion engine and fixture validation
- `store.py`: fixture and evaluation-run persistence
- `service.py`: replay and evaluation orchestration
- `runtime.py`: background execution runtime for queued evaluation runs
- `qualification.py`: publish-qualification policy helpers
- `io.py`: fixture import/export helpers
- `dev.py`: local CLI for fixture import, validation, export, and run

## Hard Boundaries

Do not change these here without coordination:

- `kernel.start_conversation(...)`
- `ConversationState`
- shared turn/event aliases in `src/ruhu/schemas.py`
- generic conversation API contracts in `src/ruhu/api.py`

The local harness in `dev.py` should stay file-backed and in-memory. It is for
developer iteration, not for introducing new shared API or realtime behavior.

The production app runtime may schedule evaluation runs asynchronously, but the
canonical execution path must still flow through `EvaluationService` and the
real kernel. Do not add a second fake runner for queued jobs.

## Local Commands

Validate fixtures against an agent document:

```bash
PYTHONPATH=src python -m ruhu.simulation_eval validate \
  --agent-document-file /path/to/agent.json \
  --fixtures-file /tmp/fixtures.json
```

Run fixtures locally:

```bash
PYTHONPATH=src python -m ruhu.simulation_eval run \
  --agent-document-file /path/to/agent.json \
  --fixtures-file /tmp/fixtures.json \
  --json
```

Normalize a fixture file into a bundle:

```bash
PYTHONPATH=src python -m ruhu.simulation_eval import \
  --input /tmp/fixture.json \
  --output /tmp/fixtures.bundle.json
```

Export one fixture out of a bundle:

```bash
PYTHONPATH=src python -m ruhu.simulation_eval export \
  --input /tmp/fixtures.bundle.json \
  --fixture-id fixture-1 \
  --single
```

## Tests

Focused verification:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src pytest \
  tests/test_simulation_eval_*.py \
  tests/test_agent_review_publish_qualification.py -q
```

Store tests use local Postgres through the shared test fixture in `tests/conftest.py`.

## Migration Discipline

The SQLAlchemy models for fixture and evaluation persistence already exist in
`src/ruhu/db_models.py`. If you add or remove columns or tables here, add a
matching Alembic revision under `alembic/versions/` and keep the Postgres RLS
policy list in sync with `src/ruhu/db.py`.

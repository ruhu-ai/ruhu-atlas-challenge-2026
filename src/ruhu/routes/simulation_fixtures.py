"""Simulation fixture endpoints — extracted from api.py (RP-3.1 step 4).

Covers /agents/{agent_id}/simulation-fixtures (list, folders, folder
move/rename, export, create, import) and /simulation-fixtures/{fixture_id}
(get, patch, delete). Registration order inside this router preserves the
original inline order (hazard H2: the static ``folders`` / ``export`` /
``import`` segments and the parameterized ``{fixture_id}`` routes keep
their relative positions).

The fixture DTOs still live in ``ruhu.api`` (they migrate with the rest of
the inline DTO block in a later step), so this module is imported by
``create_app()`` AT THE MOUNT SITE rather than at api.py's module top — a
top-level import would be circular while api.py is still mid-import.
No ``tags=`` / ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

# DTOs at module top (hazard H7: PEP 563 return annotations resolve against
# this module's globals).
from ..api import (
    FixtureFolderInfo,
    FixtureFolderMoveRequest,
    FixtureFolderRenameRequest,
    SimulationFixtureCreateRequest,
    SimulationFixtureImportRequest,
    SimulationFixtureImportResult,
    SimulationFixturePatchRequest,
)
from ..api_auth import RequestAuthContext
from ..auth_deps import make_reviewer_context_dep
from ..services.org_scope import (
    make_organization_id_for_request,
    user_id_for_context,
)
from ..simulation_eval import SimulationFixture, SimulationFixtureBundle, validate_fixture

if TYPE_CHECKING:
    from ..registry import AgentVersionSnapshot


def _raise_for_invalid_fixture(snapshot: "AgentVersionSnapshot", fixture: SimulationFixture) -> None:
    issues = validate_fixture(snapshot, fixture)
    blockers = [issue for issue in issues if issue.severity == "blocker"]
    if not blockers:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "message": "simulation fixture failed validation",
            "issues": [issue.model_dump(mode="json") for issue in issues],
        },
    )


def build_simulation_fixtures_router(
    *,
    simulation_fixture_store,
    resolve_agent_snapshot: Callable,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> APIRouter:
    """Build the simulation-fixtures router.

    ``resolve_agent_snapshot`` is create_app()'s ``_resolve_agent_snapshot``
    closure (shared with the agents/conversations groups — it stays in
    api.py until the agents-core extraction at blueprint step 10).
    """
    router = APIRouter()

    _require_runtime_reviewer_context = make_reviewer_context_dep(auth_enabled)
    _organization_id_for_request = make_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _user_id_for_context = user_id_for_context

    @router.get("/agents/{agent_id}/simulation-fixtures", response_model=list[SimulationFixture])
    def list_simulation_fixtures(
        agent_id: str,
        request: Request,
        is_active: bool | None = None,
        gate_required: bool | None = None,
        folder_path: str | None = None,
    ) -> list[SimulationFixture]:
        organization_id = _organization_id_for_request(request)
        return simulation_fixture_store.list_for_agent(
            agent_id,
            organization_id=organization_id,
            is_active=is_active,
            gate_required=gate_required,
            folder_path=folder_path,
        )

    @router.get("/agents/{agent_id}/simulation-fixtures/folders", response_model=list[FixtureFolderInfo])
    def list_simulation_fixture_folders(
        agent_id: str,
        request: Request,
    ) -> list[FixtureFolderInfo]:
        """List distinct folder paths and fixture counts for an agent."""
        organization_id = _organization_id_for_request(request)
        fixtures = simulation_fixture_store.list_for_agent(
            agent_id,
            organization_id=organization_id,
        )
        folder_counts: dict[str | None, int] = {}
        for fixture in fixtures:
            folder = fixture.folder_path
            folder_counts[folder] = folder_counts.get(folder, 0) + 1
        return [
            FixtureFolderInfo(folder_path=folder, fixture_count=count)
            for folder, count in sorted(folder_counts.items())
        ]

    @router.post("/agents/{agent_id}/simulation-fixtures/folders/move", response_model=list[SimulationFixture])
    def move_simulation_fixtures_to_folder(
        agent_id: str,
        payload: FixtureFolderMoveRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> list[SimulationFixture]:
        """Bulk-move fixtures to a different folder."""
        organization_id = _organization_id_for_request(request)
        # Pre-existing latent NameError preserved verbatim from the inline
        # block (introduced in 287b95d; the name was never defined or
        # imported). Behaviour-neutral move — see RP-3.1 step 4 notes.
        _verify_organization_access(context, organization_id)  # noqa: F821
        fixtures = simulation_fixture_store.list_for_agent(
            agent_id,
            organization_id=organization_id,
        )
        wanted = set(payload.fixture_ids)
        updated = []
        for fixture in fixtures:
            if fixture.fixture_id in wanted:
                updated_fixture = fixture.model_copy(update={"folder_path": payload.folder_path})
                simulation_fixture_store.save(updated_fixture)
                updated.append(updated_fixture)
        return updated

    @router.post("/agents/{agent_id}/simulation-fixtures/folders/rename", response_model=list[SimulationFixture])
    def rename_simulation_fixture_folder(
        agent_id: str,
        payload: FixtureFolderRenameRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> list[SimulationFixture]:
        """Bulk-rename folder prefix for all fixtures in a folder."""
        organization_id = _organization_id_for_request(request)
        # Pre-existing latent NameError preserved verbatim (see above).
        _verify_organization_access(context, organization_id)  # noqa: F821
        fixtures = simulation_fixture_store.list_for_agent(
            agent_id,
            organization_id=organization_id,
            folder_path=payload.from_path,
        )
        updated = []
        for fixture in fixtures:
            updated_fixture = fixture.model_copy(update={"folder_path": payload.to_path})
            simulation_fixture_store.save(updated_fixture)
            updated.append(updated_fixture)
        return updated

    @router.get("/agents/{agent_id}/simulation-fixtures/export", response_model=SimulationFixtureBundle)
    def export_agent_simulation_fixtures(
        agent_id: str,
        request: Request,
        fixture_id: list[str] = Query(default=[]),
        is_active: bool | None = True,
    ) -> SimulationFixtureBundle:
        organization_id = _organization_id_for_request(request)
        fixtures = simulation_fixture_store.list_for_agent(
            agent_id,
            organization_id=organization_id,
            is_active=is_active,
        )
        if fixture_id:
            wanted = set(fixture_id)
            fixtures = [fixture for fixture in fixtures if fixture.fixture_id in wanted]
        return SimulationFixtureBundle(fixtures=fixtures)

    @router.post("/agents/{agent_id}/simulation-fixtures", response_model=SimulationFixture)
    def create_simulation_fixture(
        agent_id: str,
        payload: SimulationFixtureCreateRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> SimulationFixture:
        snapshot, organization_id = resolve_agent_snapshot(
            request,
            agent_id,
            target="draft",
        )
        if payload.fixture_id:
            existing = simulation_fixture_store.load(payload.fixture_id, organization_id=organization_id)
            if existing is not None:
                raise HTTPException(status_code=409, detail="simulation fixture already exists")
        fixture = SimulationFixture(
            fixture_id=payload.fixture_id or str(uuid4()),
            organization_id=organization_id,
            agent_id=agent_id,
            name=payload.name,
            description=payload.description,
            tags=list(payload.tags),
            default_channel=payload.default_channel,
            default_modality=payload.default_modality,
            starting_step_id=payload.starting_step_id,
            starting_scenario_id=payload.starting_scenario_id,
            seed_facts=dict(payload.seed_facts),
            turns=list(payload.turns),
            assertions=list(payload.assertions),
            is_active=payload.is_active,
            gate_required=payload.gate_required,
            created_by_user_id=_user_id_for_context(context),
        )
        _raise_for_invalid_fixture(snapshot, fixture)
        simulation_fixture_store.save(fixture)
        return simulation_fixture_store.load(
            fixture.fixture_id,
            organization_id=organization_id,
        ) or fixture

    @router.get("/simulation-fixtures/{fixture_id}", response_model=SimulationFixture)
    def get_simulation_fixture(fixture_id: str, request: Request) -> SimulationFixture:
        organization_id = _organization_id_for_request(request)
        fixture = simulation_fixture_store.load(fixture_id, organization_id=organization_id)
        if fixture is None:
            raise HTTPException(status_code=404, detail="unknown simulation fixture")
        return fixture

    @router.patch("/simulation-fixtures/{fixture_id}", response_model=SimulationFixture)
    def update_simulation_fixture(
        fixture_id: str,
        payload: SimulationFixturePatchRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> SimulationFixture:
        organization_id = _organization_id_for_request(request)
        existing = simulation_fixture_store.load(fixture_id, organization_id=organization_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="unknown simulation fixture")
        snapshot, scoped_organization_id = resolve_agent_snapshot(
            request,
            existing.agent_id,
            target="draft",
        )
        candidate = existing.model_copy(deep=True)
        for field_name in payload.model_fields_set:
            setattr(candidate, field_name, getattr(payload, field_name))
        candidate.updated_at = datetime.now(timezone.utc)
        candidate.organization_id = scoped_organization_id
        _raise_for_invalid_fixture(snapshot, candidate)
        simulation_fixture_store.save(candidate)
        return simulation_fixture_store.load(
            candidate.fixture_id,
            organization_id=scoped_organization_id,
        ) or candidate

    @router.delete("/simulation-fixtures/{fixture_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_simulation_fixture(
        fixture_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> Response:
        organization_id = _organization_id_for_request(request)
        deleted = simulation_fixture_store.deactivate(fixture_id, organization_id=organization_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="unknown simulation fixture")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/agents/{agent_id}/simulation-fixtures/import", response_model=SimulationFixtureImportResult)
    def import_simulation_fixtures(
        agent_id: str,
        payload: SimulationFixtureImportRequest,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_runtime_reviewer_context),
    ) -> SimulationFixtureImportResult:
        snapshot, organization_id = resolve_agent_snapshot(
            request,
            agent_id,
            target="draft",
        )
        created_count = 0
        updated_count = 0
        imported_fixtures: list[SimulationFixture] = []
        for source_fixture in payload.bundle.fixtures:
            existing = simulation_fixture_store.load(
                source_fixture.fixture_id,
                organization_id=organization_id,
            )
            fixture_id = source_fixture.fixture_id
            if existing is not None and existing.agent_id != agent_id:
                existing = None
                fixture_id = str(uuid4())
            if payload.assign_new_ids or (existing is not None and not payload.replace_existing):
                fixture_id = str(uuid4())
                existing = None
            cloned_turns = list(source_fixture.turns)
            cloned_assertions = list(source_fixture.assertions)
            if fixture_id != source_fixture.fixture_id:
                cloned_turns = [
                    turn.model_copy(
                        update={
                            "turn_id": str(uuid4()),
                            "dedupe_key": str(uuid4()),
                        }
                    )
                    for turn in source_fixture.turns
                ]
                cloned_assertions = [
                    assertion.model_copy(update={"assertion_id": str(uuid4())})
                    for assertion in source_fixture.assertions
                ]
            fixture = source_fixture.model_copy(
                deep=True,
                update={
                    "fixture_id": fixture_id,
                    "organization_id": organization_id,
                    "agent_id": agent_id,
                    "turns": cloned_turns,
                    "assertions": cloned_assertions,
                    "is_active": (
                        source_fixture.is_active if payload.activate_imported is None else payload.activate_imported
                    ),
                    "created_by_user_id": _user_id_for_context(context),
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            if existing is None:
                fixture.created_at = datetime.now(timezone.utc)
                created_count += 1
            else:
                fixture.created_at = existing.created_at
                updated_count += 1
            _raise_for_invalid_fixture(snapshot, fixture)
            simulation_fixture_store.save(fixture)
            imported_fixtures.append(
                simulation_fixture_store.load(
                    fixture.fixture_id,
                    organization_id=organization_id,
                ) or fixture
            )
        return SimulationFixtureImportResult(
            created_count=created_count,
            updated_count=updated_count,
            imported_fixtures=imported_fixtures,
        )

    return router

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import SimulationFixture


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SimulationFixtureBundle(BaseModel):
    schema_version: Literal["simulation_fixture_bundle.v1"] = "simulation_fixture_bundle.v1"
    exported_at: datetime = Field(default_factory=_utcnow)
    fixtures: list[SimulationFixture] = Field(default_factory=list)


def export_fixture(fixture: SimulationFixture, *, indent: int = 2) -> str:
    return json.dumps(fixture.model_dump(mode="json"), indent=indent, sort_keys=True)


def export_fixtures(fixtures: list[SimulationFixture], *, indent: int = 2) -> str:
    bundle = SimulationFixtureBundle(fixtures=[fixture.model_copy(deep=True) for fixture in fixtures])
    return bundle.model_dump_json(indent=indent)


def import_fixture(payload: str | bytes | dict[str, Any]) -> SimulationFixture:
    if isinstance(payload, (str, bytes)):
        return SimulationFixture.model_validate_json(payload)
    return SimulationFixture.model_validate(payload)


def import_fixtures(payload: str | bytes | dict[str, Any]) -> list[SimulationFixture]:
    if isinstance(payload, (str, bytes)):
        bundle = SimulationFixtureBundle.model_validate_json(payload)
    else:
        bundle = SimulationFixtureBundle.model_validate(payload)
    return [fixture.model_copy(deep=True) for fixture in bundle.fixtures]

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import JourneyDefinition, JourneyDefinitionVersion
from .schemas import JourneyDefinitionBundle, JourneyDefinitionBundleEntry


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def export_definition_bundle(
    definitions: list[JourneyDefinition],
    versions_by_definition_id: dict[str, list[JourneyDefinitionVersion]],
) -> JourneyDefinitionBundle:
    entries = [
        JourneyDefinitionBundleEntry(
            definition=definition.model_copy(deep=True),
            versions=[
                version.model_copy(deep=True)
                for version in versions_by_definition_id.get(definition.definition_id, [])
            ],
        )
        for definition in definitions
    ]
    return JourneyDefinitionBundle(
        exported_at=_utcnow(),
        definitions=entries,
    )


def import_definition_bundle(payload: str | bytes | dict[str, Any] | JourneyDefinitionBundle) -> JourneyDefinitionBundle:
    if isinstance(payload, JourneyDefinitionBundle):
        return payload.model_copy(deep=True)
    if isinstance(payload, (str, bytes)):
        return JourneyDefinitionBundle.model_validate_json(payload)
    return JourneyDefinitionBundle.model_validate(payload)

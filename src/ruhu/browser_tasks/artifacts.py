from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import BrowserTask
from .worker_contracts import BrowserArtifactRef, BrowserGeneratedArtifact


class BrowserArtifactPublisher(Protocol):
    def publish_generated_artifact(
        self,
        *,
        task: BrowserTask,
        artifact: BrowserGeneratedArtifact,
    ) -> BrowserArtifactRef: ...


@dataclass(slots=True)
class AttachmentRuntimeBrowserArtifactPublisher:
    attachment_runtime: object

    def publish_generated_artifact(
        self,
        *,
        task: BrowserTask,
        artifact: BrowserGeneratedArtifact,
    ) -> BrowserArtifactRef:
        service = getattr(self.attachment_runtime, "service", None)
        if service is None:
            raise RuntimeError("attachment runtime service is not configured")
        stored = service.create_artifact(
            conversation_id=task.conversation_id,
            organization_id=task.organization_id,
            filename=artifact.filename,
            content_type=artifact.content_type,
            content_bytes=artifact.content_bytes,
            kind=_attachment_artifact_kind(artifact.kind),
            task_id=task.task_id,
            metadata={
                **dict(artifact.metadata),
                "created_via": "browser_worker",
                "browser_artifact_kind": artifact.kind,
            },
        )
        return BrowserArtifactRef(
            artifact_id=stored.artifact_id,
            kind=artifact.kind,
            uri=f"artifact:{stored.artifact_id}",
            label=artifact.label or stored.filename,
            metadata={
                "filename": stored.filename,
                "content_type": stored.content_type,
                "size_bytes": stored.size_bytes,
                "internal_download_url": f"/internal/browser-tasks/artifacts/{stored.artifact_id}/download",
                "public_widget_download_url": (
                    f"/public/widget/sessions/{task.conversation_id}/artifacts/{stored.artifact_id}/download"
                ),
            },
        )


def _attachment_artifact_kind(kind: str) -> str:
    if kind == "result_json":
        return "result_bundle"
    if kind == "action_log":
        return "log"
    return kind

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .worker_contracts import BrowserResolvedUpload, BrowserWorkerRequest


class BrowserUploadAttachmentService(Protocol):
    def get_attachment_bytes(
        self,
        *,
        attachment_id: str,
        organization_id: str | None,
    ): ...


@dataclass(slots=True)
class AttachmentBrowserUploadResolver:
    attachment_service: BrowserUploadAttachmentService

    def resolve(
        self,
        *,
        request: BrowserWorkerRequest,
        attachment_id: str,
    ) -> BrowserResolvedUpload:
        loaded = self.attachment_service.get_attachment_bytes(
            attachment_id=attachment_id,
            organization_id=request.organization_id,
        )
        if loaded is None:
            raise ValueError("browser upload attachment could not be loaded")
        attachment, content = loaded
        return BrowserResolvedUpload(
            attachment_id=attachment.attachment_id,
            filename=attachment.filename,
            content_type=attachment.content_type,
            content_bytes=content,
        )

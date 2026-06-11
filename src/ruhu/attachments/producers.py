"""Attachment view producers.

Each producer derives one ``AttachmentView`` from raw attachment bytes or an
existing view:

  GeminiFileUploader — uploads bytes to the Gemini Files API and returns a
      file URI suitable for subsequent multimodal calls
      (``native_file_uri`` view, kind="native_file_uri").

  GeminiVisionProducer — calls Gemini multimodal with either a previously
      uploaded file URI or inline base64 bytes and returns a text description
      (``vision`` view, kind="vision").

Producers are optional dependencies on ``AttachmentService``, wired by the
FastAPI factory when ``GOOGLE_API_KEY`` / ``GEMINI_API_KEY`` are present and
the ``RUHU_ATTACHMENTS_VISION_ENABLED`` flag is set.  Both are synchronous,
mock-friendly, and safe to call in a thread pool.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_GEMINI_UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"
_GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


@dataclass(slots=True)
class GeminiFileUploader:
    """Upload attachment bytes to the Gemini Files API.

    Returns the full file URI (e.g.
    ``https://generativelanguage.googleapis.com/v1beta/files/abc123``) which
    can be referenced in subsequent Gemini multimodal calls without re-sending
    the bytes.

    Parameters
    ----------
    api_key:
        Gemini API key.
    timeout_seconds:
        HTTP request timeout.  Defaults to 30 s to accommodate large uploads.
    """

    api_key: str
    timeout_seconds: float = 30.0

    def upload(
        self,
        *,
        filename: str,
        content_type: str,
        content_bytes: bytes,
    ) -> str:
        """Upload bytes and return the Gemini file URI.

        Raises
        ------
        RuntimeError
            On HTTP error or unexpected response shape.
        """
        if httpx is None:  # pragma: no cover
            raise RuntimeError("httpx is required for GeminiFileUploader")

        # Gemini Files API simple upload — multipart/related with two parts:
        # Part 1: file metadata JSON; Part 2: raw file bytes.
        boundary = "ruhu_upload_boundary"
        metadata_part = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n\r\n"
        ).encode() + json.dumps({"file": {"displayName": filename}}).encode()
        content_part = (
            f"\r\n--{boundary}\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode() + content_bytes + f"\r\n--{boundary}--".encode()
        body = metadata_part + content_part

        url = f"{_GEMINI_UPLOAD_URL}?uploadType=multipart&key={self.api_key}"
        headers = {"Content-Type": f"multipart/related; boundary={boundary}"}

        start = time.monotonic()
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, content=body, headers=headers)
        elapsed = time.monotonic() - start

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Gemini Files API upload failed: HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            data = response.json()
            uri: str = data["file"]["uri"]
        except (KeyError, ValueError) as exc:
            raise RuntimeError(
                f"Gemini Files API: unexpected response shape: {exc}"
            ) from exc

        logger.debug(
            "gemini file uploader: uploaded %s (%d bytes) in %.2fs → %s",
            filename,
            len(content_bytes),
            elapsed,
            uri,
        )
        return uri


_VISION_PROMPT = (
    "Describe this image in detail. "
    "Include: what is depicted, any visible text, key objects, and their "
    "spatial relationships. Be concise but thorough."
)


@dataclass(slots=True)
class GeminiVisionProducer:
    """Generate a text description of an image using Gemini multimodal.

    Accepts either a previously uploaded file URI (preferred for large files,
    avoids re-sending bytes) or inline base64-encoded image bytes.

    Parameters
    ----------
    api_key:
        Gemini API key.
    model:
        Gemini model name.  Defaults to ``gemini-2.0-flash`` which has strong
        vision capabilities and low latency.
    timeout_seconds:
        HTTP request timeout.
    """

    api_key: str
    model: str = "gemini-2.0-flash"
    timeout_seconds: float = 20.0

    def describe(
        self,
        *,
        file_uri: str | None = None,
        content_bytes: bytes | None = None,
        content_type: str = "image/jpeg",
    ) -> str:
        """Return a text description of the image.

        Provide either ``file_uri`` (from a prior ``GeminiFileUploader.upload``
        call) or ``content_bytes`` (encoded inline as base64).

        Raises
        ------
        ValueError
            If neither ``file_uri`` nor ``content_bytes`` is provided.
        RuntimeError
            On HTTP or API error.
        """
        if file_uri is None and content_bytes is None:
            raise ValueError("describe() requires either file_uri or content_bytes")

        if httpx is None:  # pragma: no cover
            raise RuntimeError("httpx is required for GeminiVisionProducer")

        if file_uri is not None:
            image_part: dict[str, Any] = {
                "fileData": {
                    "mimeType": content_type,
                    "fileUri": file_uri,
                }
            }
        else:
            assert content_bytes is not None
            image_part = {
                "inlineData": {
                    "mimeType": content_type,
                    "data": base64.b64encode(content_bytes).decode(),
                }
            }

        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [image_part, {"text": _VISION_PROMPT}],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 512,
            },
        }
        url = (
            f"{_GEMINI_GENERATE_URL.format(model=self.model)}"
            f"?key={self.api_key}"
        )

        start = time.monotonic()
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, json=payload)
        elapsed = time.monotonic() - start

        if response.status_code != 200:
            raise RuntimeError(
                f"Gemini vision call failed: HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            data = response.json()
            description: str = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"Gemini vision: unexpected response shape: {exc}"
            ) from exc

        logger.debug(
            "gemini vision producer: described image in %.2fs (%d chars)",
            elapsed,
            len(description),
        )
        return description

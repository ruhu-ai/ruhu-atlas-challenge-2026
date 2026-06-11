"""Phase 2d — Persona avatar upload validation + image processing.

Replaces Phase 1's "HTTPS URL only" caveat with a real upload endpoint
backed by the existing image-processing toolchain (Pillow). Pure
functions live here; the FastAPI endpoint + DB persistence live in
``api.py``.

Production-readiness contracts (each backed by tests):

* **Format allowlist** — image/jpeg, image/png, image/webp. **SVG is
  rejected** because SVG is XML and embeds JS/event handlers; serving
  it from the customer widget would be an XSS vector.
* **Size cap** — 2 MB hard limit at the endpoint AND in this module.
  Defence in depth: the proxy should also enforce.
* **Dimension validation** — square (within 5% tolerance), between
  256x256 and 1024x1024. Out-of-range → 422 with a clear message.
* **MIME-vs-magic-bytes match** — extension/MIME header is one signal;
  the actual image content is decoded by Pillow which validates the
  format. A polyglot file masquerading as PNG (e.g. ``.png`` extension
  but actually a Windows executable) gets rejected here, before it
  ever reaches storage.
* **EXIF strip** — uploaded JPEGs commonly carry GPS metadata, camera
  IDs, and other PII. We strip ALL metadata at upload time. The
  uploaded bytes that get stored are NOT the user-supplied bytes —
  they're the re-encoded, metadata-free output.
* **AV-scan seam** — a hook function the api layer can pass in;
  Phase 2d ships with a default no-op scanner, the production
  ClamAV path lands as a follow-up. The seam is correct so future
  wiring doesn't change call sites.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Callable

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)


# ── Public limits ───────────────────────────────────────────────────────────


MAX_AVATAR_BYTES = 2 * 1024 * 1024
"""Hard ceiling on the input file size. The endpoint enforces this
again before reading bytes; this constant is the canonical value."""

MIN_AVATAR_DIMENSION = 256
MAX_AVATAR_DIMENSION = 1024
SQUARE_TOLERANCE_PERCENT = 5.0
"""Pillow can return slightly different width/height even on
nominally-square images (e.g. JPEG with non-1.0 pixel-aspect-ratio
metadata). Allow up to 5% delta before rejecting."""


_ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/jpg", "image/png", "image/webp"},
)


# ── Public types ────────────────────────────────────────────────────────────


class AvatarValidationError(ValueError):
    """Raised when an upload fails validation. The api layer turns
    this into a 422 with the message included (the messages are
    user-readable on purpose — they tell the wizard UI what to say)."""


@dataclass(frozen=True, slots=True)
class ProcessedAvatar:
    """Output of ``process_avatar_upload`` — the bytes the api layer
    should persist (NOT the user-supplied bytes; the image has been
    re-encoded, EXIF-stripped, and possibly resized).
    """

    bytes: bytes
    mime: str
    width: int
    height: int


# AV scanner hook. Returns True if the bytes pass; False if quarantined.
# Default implementation is a no-op (used until ClamAV wiring lands).
AvatarAVScanner = Callable[[bytes], bool]


def _no_op_av_scanner(_data: bytes) -> bool:
    """Default scanner — accepts everything. Replace at the api layer
    with a real ClamAV check before shipping to a tenant where AV
    scanning is required by policy."""
    return True


# ── Validation pipeline ─────────────────────────────────────────────────────


def process_avatar_upload(
    *,
    raw_bytes: bytes,
    declared_mime: str,
    av_scanner: AvatarAVScanner = _no_op_av_scanner,
) -> ProcessedAvatar:
    """Validate, AV-scan, EXIF-strip, and re-encode an avatar upload.

    Raises ``AvatarValidationError`` on every guard failure with a
    user-readable message. Caller (api layer) wraps it as a 422
    response.

    Returns ``ProcessedAvatar`` with the bytes that should be
    persisted — explicitly NOT the user-supplied bytes.
    """
    # 1. Size guard — defence in depth on top of the endpoint check.
    if not raw_bytes:
        raise AvatarValidationError("avatar upload is empty")
    if len(raw_bytes) > MAX_AVATAR_BYTES:
        raise AvatarValidationError(
            f"avatar exceeds {MAX_AVATAR_BYTES} bytes (got {len(raw_bytes)}). "
            "Use a smaller image."
        )

    # 2. MIME allowlist — note this is the DECLARED MIME (what the
    # client said). The actual content is verified by Pillow below.
    declared = declared_mime.split(";")[0].strip().lower()
    if declared not in _ALLOWED_MIME_TYPES:
        raise AvatarValidationError(
            f"unsupported avatar MIME {declared_mime!r}; "
            f"use one of {sorted(_ALLOWED_MIME_TYPES)}. "
            "SVG is not allowed because it can carry executable "
            "content."
        )

    # 3. AV scan — fail closed.
    if not av_scanner(raw_bytes):
        raise AvatarValidationError(
            "avatar failed antivirus scan; choose a different file"
        )

    # 4. Decode with Pillow. This step doubles as MIME-vs-magic-bytes
    # verification — Pillow rejects polyglot files (e.g. an .exe
    # renamed to .png) because they don't decode as a valid image.
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.load()  # force decode so we catch truncation now
            actual_format = (img.format or "").lower()
            width, height = img.size
            # Re-encode without any metadata. Pillow rebuilds the
            # output stream from the raw pixel data + the format
            # header; EXIF / IPTC / XMP / ICC profiles are dropped
            # by default in this code path because we don't pass
            # ``exif=`` / ``icc_profile=`` to ``save()``.
            normalised_format = _normalise_format_for_save(actual_format)
            normalised_mime = _format_to_mime(normalised_format)
            output = io.BytesIO()
            # Convert palette / RGBA inputs sensibly:
            #  - JPEG can't handle alpha — flatten over white if needed.
            #  - PNG and WebP keep their channels.
            save_kwargs: dict = {}
            save_image = img
            if normalised_format == "JPEG":
                if img.mode in ("RGBA", "LA"):
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[-1])
                    save_image = background
                elif img.mode != "RGB":
                    save_image = img.convert("RGB")
                save_kwargs["quality"] = 90
                save_kwargs["optimize"] = True
            elif normalised_format == "PNG":
                save_kwargs["optimize"] = True
            save_image.save(output, format=normalised_format, **save_kwargs)
            stripped_bytes = output.getvalue()
    except UnidentifiedImageError as exc:
        raise AvatarValidationError(
            "avatar file is not a recognised image; the file may be "
            "corrupted or its extension may not match its content"
        ) from exc
    except AvatarValidationError:
        raise
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("persona_avatar.process_failed")
        raise AvatarValidationError(
            "could not process avatar image; try a different file"
        ) from exc

    # 5. Magic-bytes vs declared MIME — Pillow knows the actual
    # format. If the declared MIME and actual format disagree, that's
    # a polyglot signal we want to reject.
    if not _mime_matches_format(declared, normalised_format):
        raise AvatarValidationError(
            f"avatar MIME {declared!r} does not match its content "
            f"(actual: {normalised_format})"
        )

    # 6. Dimension guards.
    _validate_dimensions(width=width, height=height)

    return ProcessedAvatar(
        bytes=stripped_bytes,
        mime=normalised_mime,
        width=width,
        height=height,
    )


# ── Internal helpers ─────────────────────────────────────────────────────────


def _validate_dimensions(*, width: int, height: int) -> None:
    if width < MIN_AVATAR_DIMENSION or height < MIN_AVATAR_DIMENSION:
        raise AvatarValidationError(
            f"avatar must be at least {MIN_AVATAR_DIMENSION}x"
            f"{MIN_AVATAR_DIMENSION} (got {width}x{height})"
        )
    if width > MAX_AVATAR_DIMENSION or height > MAX_AVATAR_DIMENSION:
        raise AvatarValidationError(
            f"avatar must be at most {MAX_AVATAR_DIMENSION}x"
            f"{MAX_AVATAR_DIMENSION} (got {width}x{height})"
        )
    longer = max(width, height)
    shorter = min(width, height)
    delta_pct = ((longer - shorter) / longer) * 100.0
    if delta_pct > SQUARE_TOLERANCE_PERCENT:
        raise AvatarValidationError(
            f"avatar must be square (within {SQUARE_TOLERANCE_PERCENT:.0f}% "
            f"tolerance); got {width}x{height}"
        )


def _normalise_format_for_save(actual_format: str) -> str:
    """Pillow uses uppercase format identifiers internally. Map the
    detected format to the canonical one Pillow's ``save()`` accepts."""
    fmt = actual_format.upper()
    return {"JPG": "JPEG"}.get(fmt, fmt)


def _format_to_mime(pil_format: str) -> str:
    return {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }.get(pil_format, "application/octet-stream")


def _mime_matches_format(declared_mime: str, pil_format: str) -> bool:
    expected = _format_to_mime(pil_format)
    if declared_mime == expected:
        return True
    # JPEG aliases.
    if pil_format == "JPEG" and declared_mime in ("image/jpeg", "image/jpg"):
        return True
    return False


__all__ = [
    "AvatarAVScanner",
    "AvatarValidationError",
    "MAX_AVATAR_BYTES",
    "MAX_AVATAR_DIMENSION",
    "MIN_AVATAR_DIMENSION",
    "ProcessedAvatar",
    "SQUARE_TOLERANCE_PERCENT",
    "process_avatar_upload",
]

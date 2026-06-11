from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from .models import ExtractedKnowledgeDocument, KnowledgeFileKind

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency
    yaml = None

try:
    import pypdf
except ImportError:  # pragma: no cover - optional dependency
    pypdf = None


_TEXT_EXTENSIONS: dict[str, KnowledgeFileKind] = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".csv": "csv",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".docx": "docx",
    ".pdf": "pdf",
}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._parts)


def supported_document_extensions() -> tuple[str, ...]:
    return tuple(sorted(_TEXT_EXTENSIONS))


def detect_file_kind(filename: str) -> KnowledgeFileKind:
    extension = Path(filename).suffix.lower()
    return _TEXT_EXTENSIONS.get(extension, "binary")


def _best_effort_decode(file_bytes: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("File content could not be decoded as text.")


def _guess_media_type(filename: str) -> str | None:
    media_type, _ = mimetypes.guess_type(filename)
    return media_type


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return stem or "Untitled document"


def _summarize_text(text: str, *, max_chars: int = 220) -> str | None:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return None
    return compact if len(compact) <= max_chars else compact[: max_chars - 1].rstrip() + "…"


def _extract_json(file_bytes: bytes) -> str:
    decoded = _best_effort_decode(file_bytes)
    payload = json.loads(decoded)
    return json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True)


def _extract_yaml(file_bytes: bytes) -> str:
    decoded = _best_effort_decode(file_bytes)
    if yaml is None:
        return decoded
    payload = yaml.safe_load(decoded)
    if payload is None:
        return ""
    return json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True)


def _extract_csv(file_bytes: bytes) -> str:
    decoded = _best_effort_decode(file_bytes)
    reader = csv.reader(io.StringIO(decoded))
    return "\n".join(", ".join(cell.strip() for cell in row if cell is not None) for row in reader)


def _extract_html(file_bytes: bytes) -> str:
    decoded = _best_effort_decode(file_bytes)
    parser = _HTMLTextExtractor()
    parser.feed(decoded)
    return parser.text()


def _extract_xml(file_bytes: bytes) -> str:
    decoded = _best_effort_decode(file_bytes)
    root = ElementTree.fromstring(decoded)
    texts = [part.strip() for part in root.itertext() if part.strip()]
    return "\n".join(texts)


def _extract_docx(file_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
        try:
            xml_bytes = archive.read("word/document.xml")
        except KeyError as exc:  # pragma: no cover - malformed docx
            raise ValueError("DOCX file is missing word/document.xml.") from exc
    root = ElementTree.fromstring(xml_bytes)
    texts = [part.strip() for part in root.itertext() if part.strip()]
    return "\n".join(texts)


def _extract_pdf(file_bytes: bytes) -> str:
    if pypdf is None:
        raise ValueError("PDF extraction requires pypdf to be installed.")
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        parts = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pragma: no cover - depends on parser internals
        raise ValueError("PDF extraction failed.") from exc
    return "\n".join(part.strip() for part in parts if part.strip())


def extract_knowledge_file(
    *,
    filename: str,
    file_bytes: bytes,
    title: str | None = None,
) -> ExtractedKnowledgeDocument:
    file_kind = detect_file_kind(filename)
    media_type = _guess_media_type(filename)
    effective_title = title or _title_from_filename(filename)

    if file_kind in {"text", "markdown"}:
        content = _best_effort_decode(file_bytes)
    elif file_kind == "json":
        content = _extract_json(file_bytes)
    elif file_kind == "yaml":
        content = _extract_yaml(file_bytes)
    elif file_kind == "csv":
        content = _extract_csv(file_bytes)
    elif file_kind == "html":
        content = _extract_html(file_bytes)
    elif file_kind == "xml":
        content = _extract_xml(file_bytes)
    elif file_kind == "docx":
        content = _extract_docx(file_bytes)
    elif file_kind == "pdf":
        content = _extract_pdf(file_bytes)
    else:
        raise ValueError(f"Unsupported document type for {filename}.")

    return ExtractedKnowledgeDocument(
        title=effective_title,
        content=content,
        summary=_summarize_text(content),
        file_kind=file_kind,
        media_type=media_type,
        metadata={
            "filename": filename,
            "size_bytes": len(file_bytes),
        },
    )

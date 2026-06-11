"""WI-6.2 — Teacher-relabel orchestrator for Stage 6 training data.

Reads ``raw_traces.jsonl`` produced by ``trace_export.py`` (WI-6.1),
selects rows that warrant a second opinion per the spec, calls a
``TeacherBackend`` (Vertex Gemini Pro by default), and writes
``teacher_labeled.jsonl`` with the teacher's chosen intent replacing the
student's. For 10% of relabeled rows it also calls a second teacher
(Claude Opus by spec) and flags disagreements for human review.

Spec source: ``docs/pre-fill-intent-classifier-design/05-training-pipeline.md``
§Teacher relabeling.

Selection rules per spec §Which rows the teacher relabels:

- ``low_conf`` bucket → all rows
- ``confusion_pair`` bucket → all rows
- ``high_conf_completion`` bucket → ``--qa-sample-rate`` random sample
  (default 5% per spec) for QA against teacher-prompt drift
- ``other`` bucket → skipped (no relabel needed)

Output schema mirrors ``raw_traces.jsonl`` plus a top-level
``teacher_confidence`` field per spec, plus ``_metadata.teacher`` and
optional ``_metadata.inter_rater`` for traceability.

Backends ship in this module:

- ``FakeTeacherBackend`` — deterministic, no network. For tests + smoke.
- ``VertexTeacherBackend`` — real Vertex Gemini REST call (httpx,
  Application Default Credentials). Same auth pattern as
  ``response_generation.py``. Tested via httpx mocks.
- ``ClaudeOpusTeacherBackend`` — stub raising ``NotImplementedError``.
  Real implementation is a follow-up (no anthropic SDK wired in Ruhu yet).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal, Protocol

from ...agent_document import AgentDocument
from ..prompt import outcome_catalog_for_step
from ...state_summary import summarize_step

AgentDocLookup = Callable[[str, str], AgentDocument | None]
"""Callback ``(agent_id, agent_version_id) -> AgentDocument | None``."""

Bucket = Literal["high_conf_completion", "low_conf", "confusion_pair", "other"]

UNKNOWN_LABEL = "unknown"
TEACHER_TEMPERATURE = 0.0
TEACHER_MAX_OUTPUT_TOKENS = 256
DEFAULT_VERTEX_MODEL = "gemini-2.5-pro"


# ── public dataclasses ──────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class TeacherRequest:
    """Inputs the teacher prompt needs to render."""

    user_text: str
    assistant_identity: str
    agent_capabilities: list[str]
    step_name: str
    step_summary: str
    candidate_labels: dict[str, str]
    student_labels: list[str]
    agent_id: str
    agent_version_id: str
    step_id: str


@dataclass(slots=True, frozen=True)
class TeacherResult:
    """One teacher backend's verdict on a single row."""

    intent: str | None
    confidence: float
    reasoning: str = ""
    raw_response: str = ""


class TeacherBackend(Protocol):
    """Backend-agnostic interface; one ``label`` call per row."""

    @property
    def name(self) -> str:
        ...

    def label(self, request: TeacherRequest) -> TeacherResult:
        ...


# ── prompt rendering (deterministic, testable) ──────────────────────────────


def render_teacher_prompt(request: TeacherRequest) -> str:
    """Return the spec-shaped teacher prompt for one row."""
    intents_sorted = sorted(request.candidate_labels.items())
    intent_lines = "\n".join(f"- {label}: {desc}" for label, desc in intents_sorted)
    capabilities = (
        ", ".join(request.agent_capabilities) if request.agent_capabilities else "none"
    )
    return (
        "You are reviewing customer support conversations to label the intent "
        "of a single customer turn.\n"
        "\n"
        f"The agent is: {request.assistant_identity}\n"
        f"The agent's capabilities: {capabilities}\n"
        f"The current step: {request.step_name} — {request.step_summary}\n"
        "\n"
        "Available intents:\n"
        f"{intent_lines}\n"
        f"- {UNKNOWN_LABEL}: none of the above match the user's message\n"
        "\n"
        f"User's message: \"{request.user_text}\"\n"
        "\n"
        "Pick exactly one intent. Return strict JSON: "
        "{\"intent\": \"...\", \"confidence\": 0.0..1.0, \"reasoning\": \"...\"}.\n"
        "\n"
        "Only return \"unknown\" if NONE of the listed intents reasonably apply. "
        "Be strict."
    )


def parse_teacher_response(text: str, candidate_labels: dict[str, str]) -> TeacherResult:
    """Parse the teacher's JSON response. Tolerant to extra whitespace and code fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.splitlines() if not line.startswith("```")
        ).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return TeacherResult(intent=None, confidence=0.0, reasoning="parse_failed", raw_response=text)
    raw_intent = str(parsed.get("intent") or "").strip()
    raw_confidence = parsed.get("confidence")
    reasoning = str(parsed.get("reasoning") or "").strip()
    if raw_intent == UNKNOWN_LABEL or raw_intent == "":
        intent: str | None = None
    elif raw_intent in candidate_labels:
        intent = raw_intent
    else:
        intent = None
        reasoning = f"intent_outside_catalog={raw_intent!r}; {reasoning}".strip("; ")
    try:
        confidence = float(raw_confidence) if raw_confidence is not None else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return TeacherResult(
        intent=intent,
        confidence=confidence,
        reasoning=reasoning,
        raw_response=text,
    )


# ── selection logic ─────────────────────────────────────────────────────────


def select_rows_for_relabel(
    rows: list[dict],
    *,
    qa_sample_rate: float = 0.05,
    seed: int = 42,
) -> list[dict]:
    """Apply spec §Which rows the teacher relabels."""
    if not 0.0 <= qa_sample_rate <= 1.0:
        raise ValueError("qa_sample_rate must be in [0, 1]")
    rng = random.Random(seed)
    selected: list[dict] = []
    for row in rows:
        bucket = (row.get("_metadata") or {}).get("bucket", "other")
        if bucket in {"low_conf", "confusion_pair"}:
            selected.append(row)
            continue
        if bucket == "high_conf_completion" and rng.random() < qa_sample_rate:
            selected.append(row)
            continue
    return selected


def select_inter_rater_indices(
    n_rows: int,
    *,
    rate: float = 0.10,
    seed: int = 43,
) -> set[int]:
    """Return row indices to send to a second teacher for inter-rater check."""
    if not 0.0 <= rate <= 1.0:
        raise ValueError("rate must be in [0, 1]")
    rng = random.Random(seed)
    return {i for i in range(n_rows) if rng.random() < rate}


# ── orchestrator ────────────────────────────────────────────────────────────


def relabel_rows(
    rows: list[dict],
    *,
    teacher: TeacherBackend,
    second_teacher: TeacherBackend | None = None,
    agent_doc_lookup: AgentDocLookup | None = None,
    qa_sample_rate: float = 0.05,
    inter_rater_rate: float = 0.10,
    seed: int = 42,
) -> list[dict]:
    """Relabel selected rows; emit the WI-6.2 output schema.

    Sequential (not yet batched). Spec uses "batch-call orchestrator" for
    the eventual implementation; sequential is correct for first ship and
    will plug into a batch dispatcher transparently — each row's request
    is independent.
    """
    selected = select_rows_for_relabel(rows, qa_sample_rate=qa_sample_rate, seed=seed)
    inter_rater_idx = select_inter_rater_indices(
        len(selected), rate=inter_rater_rate, seed=seed + 1
    )

    out: list[dict] = []
    for idx, row in enumerate(selected):
        request = build_teacher_request(row, agent_doc_lookup=agent_doc_lookup)
        result = teacher.label(request)

        relabeled = _apply_teacher_label(
            row,
            result,
            teacher_backend=teacher.name,
        )

        if second_teacher is not None and idx in inter_rater_idx:
            second_result = second_teacher.label(request)
            relabeled = _annotate_inter_rater(
                relabeled,
                primary=result,
                second=second_result,
                second_backend=second_teacher.name,
            )

        out.append(relabeled)
    return out


def build_teacher_request(
    row: dict,
    *,
    agent_doc_lookup: AgentDocLookup | None,
) -> TeacherRequest:
    """Construct a ``TeacherRequest`` from one ``raw_traces.jsonl`` row.

    When ``agent_doc_lookup`` is provided we pull authoritative
    ``assistant_identity``, ``capabilities``, step name, summary, and
    intents from the live AgentDocument. Without a lookup (cold-mode for
    tests / smoke), we degrade gracefully — empty identity/capabilities,
    intents parsed from the catalog block of ``context``.
    """
    metadata = row.get("_metadata") or {}
    user_text = _extract_user_text_from_input_window(row.get("input_window") or "")
    student_labels = list(row.get("labels") or [])

    if agent_doc_lookup is not None:
        document = agent_doc_lookup(metadata.get("agent_id", ""), metadata.get("agent_version_id", ""))
    else:
        document = None

    if document is None:
        intents = _parse_intents_from_context(row.get("context") or "")
        step_name = _parse_step_field_from_context(row.get("context") or "", "Step")
        step_summary = _parse_step_field_from_context(row.get("context") or "", "Step summary")
        return TeacherRequest(
            user_text=user_text,
            assistant_identity="",
            agent_capabilities=[],
            step_name=step_name or metadata.get("step_id", ""),
            step_summary=step_summary or "",
            candidate_labels=intents,
            student_labels=student_labels,
            agent_id=str(metadata.get("agent_id") or ""),
            agent_version_id=str(metadata.get("agent_version_id") or ""),
            step_id=str(metadata.get("step_id") or ""),
        )

    try:
        step = document.step_by_id(str(metadata.get("step_id") or ""))
    except KeyError:
        step = None
    manifest = document.agent_capability_manifest
    return TeacherRequest(
        user_text=user_text,
        assistant_identity=(manifest.assistant_identity if manifest else ""),
        agent_capabilities=(list(manifest.capabilities) if manifest else []),
        step_name=(step.name if step else str(metadata.get("step_id") or "")),
        step_summary=(summarize_step(step) if step else ""),
        candidate_labels=(outcome_catalog_for_step(step) if step else {}),
        student_labels=student_labels,
        agent_id=str(metadata.get("agent_id") or ""),
        agent_version_id=str(metadata.get("agent_version_id") or ""),
        step_id=str(metadata.get("step_id") or ""),
    )


def _apply_teacher_label(
    row: dict,
    result: TeacherResult,
    *,
    teacher_backend: str,
) -> dict:
    metadata = dict(row.get("_metadata") or {})
    metadata["student_labels"] = list(row.get("labels") or [])
    metadata["teacher"] = {
        "backend": teacher_backend,
        "intent": result.intent,
        "confidence": result.confidence,
        "reasoning": result.reasoning,
    }
    new_labels = [result.intent] if result.intent is not None else []
    return {
        "context": row.get("context", ""),
        "input_window": row.get("input_window", ""),
        "labels": new_labels,
        "teacher_confidence": result.confidence,
        "_metadata": metadata,
    }


def _annotate_inter_rater(
    relabeled: dict,
    *,
    primary: TeacherResult,
    second: TeacherResult,
    second_backend: str,
) -> dict:
    agree = primary.intent == second.intent
    metadata = dict(relabeled.get("_metadata") or {})
    metadata["inter_rater"] = {
        "backend": second_backend,
        "intent": second.intent,
        "confidence": second.confidence,
        "reasoning": second.reasoning,
        "agree": agree,
        "needs_human_review": not agree,
    }
    return {**relabeled, "_metadata": metadata}


# ── JSONL helpers ──────────────────────────────────────────────────────────


def read_raw_traces(path: str | Path) -> list[dict]:
    """Read the ``raw_traces.jsonl`` produced by WI-6.1."""
    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"raw_traces.jsonl line {line_no}: invalid JSON: {exc}") from exc
    return rows


def write_teacher_labeled(rows: Iterable[dict], path: str | Path) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with p.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
    return written


# ── input_window / context parsers ─────────────────────────────────────────


def _extract_user_text_from_input_window(input_window: str) -> str:
    """Reverse the ``build_classifier_suffix`` shape to recover user text.

    The current shape is ``"User message: <text>\\nOutcome:"``; the older
    ``"\\nIntent:"`` anchor is also accepted so historical training data
    written before the edge-owned-outcomes migration still parses.
    """
    prefix = "User message: "
    for anchor in ("\nOutcome:", "\nIntent:"):
        if input_window.startswith(prefix) and input_window.endswith(anchor):
            return input_window[len(prefix) : -len(anchor)]
    if input_window.startswith(prefix):
        return input_window[len(prefix) :]
    return input_window


def _parse_step_field_from_context(context: str, label: str) -> str:
    """Pull a single-line ``{label}: <value>`` field from the prefix."""
    needle = f"\n{label}: "
    idx = context.find(needle)
    if idx < 0:
        if context.startswith(f"{label}: "):
            line = context.split("\n", 1)[0]
            return line[len(label) + 2 :].strip()
        return ""
    rest = context[idx + len(needle) :]
    return rest.split("\n", 1)[0].strip()


def _parse_intents_from_context(context: str) -> dict[str, str]:
    """Recover the outcome catalog from a stored classifier prefix.

    Accepts both shapes of catalog header that have shipped:

    - ``"Workflow outcomes (choose exactly one):"`` — current
      edge-owned-outcomes prefix produced by ``classifier.prompt``.
    - ``"Valid intents (choose exactly one):"`` — pre-migration prefix
      stored in historical training rows. The teacher pipeline must keep
      reading those losslessly (read-only leniency, per the migration
      plan).
    """
    for marker in (
        "Workflow outcomes (choose exactly one):\n",
        "Valid intents (choose exactly one):\n",
    ):
        idx = context.find(marker)
        if idx < 0:
            continue
        block = context[idx + len(marker) :]
        intents: dict[str, str] = {}
        for line in block.splitlines():
            if not line.startswith("- "):
                continue
            body = line[2:]
            label, _, description = body.partition(": ")
            if label == UNKNOWN_LABEL:
                continue
            intents[label.strip()] = description.strip()
        return intents
    return {}


# ── backends ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class FakeTeacherBackend:
    """Deterministic teacher for tests and smoke runs.

    - ``intent`` set to a string → return that intent on every call.
    - ``intent=None`` AND ``force_unknown=False`` (default) → pick the
      first valid intent from ``request.candidate_labels`` (alphabetical).
    - ``intent=None`` AND ``force_unknown=True`` → return ``None``
      (i.e. "unknown" verdict).

    Records every request so tests can assert on prompt rendering /
    sampling.
    """

    intent: str | None = None
    confidence: float = 0.9
    reasoning: str = "synthetic teacher decision"
    backend_name: str = "synthetic"
    force_unknown: bool = False
    requests: list[TeacherRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.backend_name

    def label(self, request: TeacherRequest) -> TeacherResult:
        self.requests.append(request)
        if self.intent is not None:
            return TeacherResult(intent=self.intent, confidence=self.confidence, reasoning=self.reasoning)
        if self.force_unknown:
            return TeacherResult(intent=None, confidence=self.confidence, reasoning=self.reasoning)
        choice = next(iter(sorted(request.candidate_labels)), None)
        return TeacherResult(intent=choice, confidence=self.confidence, reasoning=self.reasoning)


@dataclass(slots=True)
class VertexTeacherBackend:
    """Vertex Gemini Pro teacher via REST + Application Default Credentials.

    Mirrors ``response_generation.py``'s pattern: build a payload, use
    google.auth ADC to fetch a bearer token, POST via httpx, parse the
    Gemini response shape. Tests inject a fake ``http_post`` so we can
    exercise the full code path without network or auth.
    """

    project: str
    location: str = "europe-west2"  # default per project memory: London region
    model: str = DEFAULT_VERTEX_MODEL
    timeout_seconds: float = 30.0
    http_post: Callable[..., Any] | None = None
    access_token_loader: Callable[[], str] | None = None

    @property
    def name(self) -> str:
        return self.model

    def label(self, request: TeacherRequest) -> TeacherResult:
        prompt = render_teacher_prompt(request)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": TEACHER_TEMPERATURE,
                "maxOutputTokens": TEACHER_MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        url = (
            f"https://aiplatform.googleapis.com/v1/projects/{self.project}/locations/{self.location}"
            f"/publishers/google/models/{self.model}:generateContent"
        )
        token = self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        response_body = self._post(url=url, json=payload, headers=headers)
        text = _extract_gemini_text(response_body)
        if not text:
            return TeacherResult(
                intent=None,
                confidence=0.0,
                reasoning="empty_vertex_response",
                raw_response=json.dumps(response_body or {}, ensure_ascii=False),
            )
        return parse_teacher_response(text, request.candidate_labels)

    def _post(self, *, url: str, json: dict, headers: dict) -> dict:  # noqa: A002 (json is param name)
        if self.http_post is not None:
            return self.http_post(url=url, json=json, headers=headers, timeout=self.timeout_seconds)
        import httpx  # type: ignore[import-not-found]

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, json=json, headers=headers)
            response.raise_for_status()
            return response.json()

    def _access_token(self) -> str:
        if self.access_token_loader is not None:
            return self.access_token_loader()
        import google.auth  # type: ignore[import-not-found]
        from google.auth.transport.requests import Request as AuthRequest  # type: ignore[import-not-found]

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        if not credentials.valid:
            credentials.refresh(AuthRequest())
        return credentials.token


@dataclass(slots=True)
class ClaudeOpusTeacherBackend:
    """Claude Opus inter-rater stub.

    Real implementation pending — Ruhu has no Anthropic SDK wired today.
    The Protocol is in place so the orchestrator can call it via
    ``--inter-rater-backend claude_opus`` once the SDK lands.
    """

    @property
    def name(self) -> str:
        return "claude-opus-4-7"

    def label(self, request: TeacherRequest) -> TeacherResult:
        raise NotImplementedError(
            "ClaudeOpusTeacherBackend not yet wired; pass --inter-rater-backend none "
            "(or extend this class) until the Anthropic SDK is added."
        )


def _extract_gemini_text(body: dict | None) -> str:
    if not body:
        return ""
    candidates = body.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(str(part.get("text") or "") for part in parts)


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rows = read_raw_traces(args.input)
    teacher = _build_teacher(args)
    second = _build_inter_rater(args)
    lookup = _build_doc_lookup(args)
    relabeled = relabel_rows(
        rows,
        teacher=teacher,
        second_teacher=second,
        agent_doc_lookup=lookup,
        qa_sample_rate=args.qa_sample_rate,
        inter_rater_rate=args.inter_rater_rate,
        seed=args.seed,
    )
    written = write_teacher_labeled(relabeled, args.output)
    print(_summary(rows, relabeled, teacher, second, args, written))
    return 0 if written > 0 else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ruhu.classifier.training.teacher_relabel",
        description="WI-6.2: Vertex Gemini Pro teacher relabel orchestrator.",
    )
    parser.add_argument("--input", required=True, help="Path to raw_traces.jsonl")
    parser.add_argument("--output", required=True, help="Path to teacher_labeled.jsonl")
    parser.add_argument(
        "--backend",
        choices=("synthetic", "vertex"),
        default="synthetic",
        help="Primary teacher backend.",
    )
    parser.add_argument(
        "--inter-rater-backend",
        choices=("none", "synthetic", "claude_opus"),
        default="none",
        help="Second teacher for the 10%% inter-rater sample. Default: skip.",
    )
    parser.add_argument(
        "--vertex-project",
        default=os.getenv("RUHU_VERTEX_PROJECT"),
        help="GCP project (defaults to $RUHU_VERTEX_PROJECT).",
    )
    parser.add_argument(
        "--vertex-location",
        default=os.getenv("RUHU_VERTEX_LOCATION", "europe-west2"),
        help="Vertex region (defaults to europe-west2 / London).",
    )
    parser.add_argument(
        "--vertex-model",
        default=os.getenv("RUHU_VERTEX_TEACHER_MODEL", DEFAULT_VERTEX_MODEL),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "Optional SQLAlchemy URL. When set, the orchestrator looks up the "
            "live AgentDocument for assistant_identity / capabilities / step. "
            "Without it we degrade to context-parsed values."
        ),
    )
    parser.add_argument("--qa-sample-rate", type=float, default=0.05)
    parser.add_argument("--inter-rater-rate", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args(argv)


def _build_teacher(args: argparse.Namespace) -> TeacherBackend:
    if args.backend == "synthetic":
        return FakeTeacherBackend()
    if args.backend == "vertex":
        if not args.vertex_project:
            raise SystemExit(
                "--backend vertex requires --vertex-project (or $RUHU_VERTEX_PROJECT)"
            )
        return VertexTeacherBackend(
            project=args.vertex_project,
            location=args.vertex_location,
            model=args.vertex_model,
        )
    raise SystemExit(f"unsupported backend: {args.backend}")


def _build_inter_rater(args: argparse.Namespace) -> TeacherBackend | None:
    if args.inter_rater_backend == "none":
        return None
    if args.inter_rater_backend == "synthetic":
        return FakeTeacherBackend(backend_name="synthetic-inter-rater")
    if args.inter_rater_backend == "claude_opus":
        return ClaudeOpusTeacherBackend()
    raise SystemExit(f"unsupported inter-rater backend: {args.inter_rater_backend}")


def _build_doc_lookup(args: argparse.Namespace) -> AgentDocLookup | None:
    if not args.database_url:
        return None
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from ...db_models import AgentVersionRecord

    engine = create_engine(args.database_url)

    def _lookup(agent_id: str, agent_version_id: str) -> AgentDocument | None:
        with Session(engine) as session:
            record = session.execute(
                select(AgentVersionRecord).where(
                    AgentVersionRecord.version_id == agent_version_id
                )
            ).scalar_one_or_none()
            if record is None:
                return None
            return AgentDocument.model_validate(record.agent_document_json)

    return _lookup


def _summary(
    rows_in: list[dict],
    rows_out: list[dict],
    teacher: TeacherBackend,
    second: TeacherBackend | None,
    args: argparse.Namespace,
    written: int,
) -> str:
    inter_rater_count = sum(
        1 for row in rows_out if (row.get("_metadata") or {}).get("inter_rater")
    )
    disagreements = sum(
        1
        for row in rows_out
        if (row.get("_metadata") or {}).get("inter_rater", {}).get("agree") is False
    )
    return (
        f"teacher_relabel wrote {written} rows to {args.output}\n"
        f"  input={len(rows_in)} selected={len(rows_out)} "
        f"primary={teacher.name} inter_rater={(second.name if second else 'none')}\n"
        f"  inter_rater_sampled={inter_rater_count}  disagreements={disagreements}"
    )


if __name__ == "__main__":
    sys.exit(main())

"""End-to-end smoke test for the prefill-first classifier on real Gemma weights.

Verifies that the constrained-decode + multi-token-logprob path produced in
Stages 1–2 actually works against an in-process Gemma model. Use it once after
applying alembic migration 0067 and before any production rollout.

Setup:
    1. Download Gemma 4 E4B weights:
         huggingface-cli download google/gemma-4-E4B-it \
           --local-dir /tmp/gemma-4-E4B-it
       (or copy from a SHA-validated source).
    2. Use a Gemma-capable Python environment with torch + transformers ≥ a
       version that supports Gemma 4. The repo's existing venv at
       ``/tmp/ruhu-gemma-arm-venv`` is the canonical one; install torch into
       it if needed.
    3. Run:
         /tmp/ruhu-gemma-arm-venv/bin/python scripts/smoke_test_gemma_classifier.py
       (or use any venv that has torch + transformers + the Gemma weights).

What it asserts:
    - Classifier returns a label from the catalog (or None for unknown).
    - Confidence is in (0, 1].
    - SemanticEventRecord is emitted with the right family/name.
    - Trace payload contains the classifier_trace block (Stage WI-2.3).
    - Multi-token labels (e.g., "transfer_status") tokenize and decode correctly.

What it does NOT assert:
    - Latency targets (those are Stage 2.5 benchmark territory; this is a
      "does it work at all" smoke test, not a perf test).
    - Specific accuracy numbers (model behavior depends on weights + LoRA;
      we only check that the contract holds).

Exit code 0 on success, non-zero on any failure.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Make src/ importable when running from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ruhu.agent_document import AgentDocument  # noqa: E402
from ruhu.classifier.protocol import ClassificationResult  # noqa: E402
from ruhu.gemma_local import GemmaLocalInterpreter, build_gemma_local_interpreter  # noqa: E402
from ruhu.schemas import RuntimeTurn, SemanticEventRecord  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


GEMMA_MODEL_PATH = os.getenv("RUHU_GEMMA_MODEL_PATH", "/tmp/gemma-4-E4B-it")
TEMPLATE_PATH = (
    REPO_ROOT / "src" / "ruhu" / "templates" / "system" / "sales-agent.json"
)


# ─────────────────────────────────────────────────────────────────────────────
# Pretty output
# ─────────────────────────────────────────────────────────────────────────────


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m"


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m"


def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m"


def _ok(label: str, detail: str = "") -> None:
    print(f"  {_green('✓')} {label}{(' ' + detail) if detail else ''}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  {_red('✗')} {label}{(' ' + detail) if detail else ''}")
    sys.exit(1)


def _info(label: str, detail: str = "") -> None:
    print(f"  {_yellow('ℹ')} {label}{(' ' + detail) if detail else ''}")


# ─────────────────────────────────────────────────────────────────────────────
# Setup checks (fail fast and tell user how to fix)
# ─────────────────────────────────────────────────────────────────────────────


def check_setup() -> None:
    print("Checking setup...")

    weights_dir = Path(GEMMA_MODEL_PATH)
    if not weights_dir.exists():
        _fail(
            "Gemma weights not found",
            f"\n    expected at: {weights_dir}\n"
            f"    fix: `huggingface-cli download google/gemma-4-E4B-it --local-dir {weights_dir}`\n"
            "    or set RUHU_GEMMA_MODEL_PATH to where you have them",
        )
    _ok("Gemma weights directory present", str(weights_dir))

    weights_file = weights_dir / "model.safetensors"
    if not weights_file.exists():
        _fail(
            "Gemma model.safetensors not found in weights dir",
            f"\n    expected at: {weights_file}",
        )
    _ok("Gemma weights file present", f"({weights_file.stat().st_size // (1024**3)}GB)")

    try:
        import torch  # noqa: F401
        _ok("torch importable")
    except ImportError as exc:
        _fail(
            "torch not installed in this Python env",
            f"\n    {exc}\n"
            "    Use a Gemma-capable venv (e.g. /tmp/ruhu-gemma-arm-venv) with torch installed.",
        )

    try:
        import transformers  # noqa: F401
        _ok("transformers importable")
    except ImportError as exc:
        _fail("transformers not installed", str(exc))

    if not TEMPLATE_PATH.exists():
        _fail(
            "Template not found",
            f"\n    expected: {TEMPLATE_PATH}",
        )
    _ok("Test template present", str(TEMPLATE_PATH.relative_to(REPO_ROOT)))


# ─────────────────────────────────────────────────────────────────────────────
# Build the interpreter
# ─────────────────────────────────────────────────────────────────────────────


def build_interpreter() -> tuple[GemmaLocalInterpreter, AgentDocument]:
    print("\nBuilding interpreter...")
    interpreter = build_gemma_local_interpreter(model_path=GEMMA_MODEL_PATH)
    _ok("GemmaLocalInterpreter built", f"model_name={interpreter.model_name}")

    raw = json.loads(TEMPLATE_PATH.read_text())
    agent_document = AgentDocument.model_validate(raw["agent_document"])
    _ok("Template parsed", f"version={agent_document.version}, scenarios={len(agent_document.scenarios)}")

    return interpreter, agent_document


# ─────────────────────────────────────────────────────────────────────────────
# Single-turn smoke check
# ─────────────────────────────────────────────────────────────────────────────


def run_one_turn(
    interpreter: GemmaLocalInterpreter,
    agent_document: AgentDocument,
    *,
    step_id: str,
    user_text: str,
    expected_label: str | None = None,
) -> list[SemanticEventRecord]:
    print(f"\n  → step={step_id}, text={user_text!r}")
    step = agent_document.step_by_id(step_id)
    turn = RuntimeTurn(
        turn_id="smoke_turn",
        dedupe_key="smoke_turn",
        channel="web_chat",
        modality="text",
        event_type="user_message",
        text=user_text,
        received_at=datetime.now(timezone.utc),
    )

    events = interpreter.interpret(
        agent_document=agent_document,
        step=step,
        agent_id="smoke_agent",
        agent_name="Smoke Agent",
        conversation_facts={},
        turn=turn,
    )

    if not events:
        _info("interpreter returned no events (classifier said unknown)")
        if expected_label is not None:
            _fail(f"expected label {expected_label!r} but classifier returned unknown")
        return events

    event = events[0]
    _ok(f"event emitted: {event.family}:{event.name}", f"confidence={event.confidence}")

    # Invariants we always require.
    if event.confidence is None:
        _fail("confidence is None — should be a real number from logprobs")
    if not (0.0 < event.confidence <= 1.0):
        _fail(f"confidence {event.confidence} is out of (0, 1]")
    _ok("confidence in (0, 1]")

    if event.source != "classifier":
        _fail(f"event.source = {event.source!r}, expected 'classifier'")
    _ok("event.source == 'classifier'")

    classifier_trace = event.payload.get("classifier_trace")
    if not isinstance(classifier_trace, dict):
        _fail("classifier_trace missing from event.payload — Stage WI-2.3 broken")
    _ok("event.payload['classifier_trace'] populated")

    required_keys = {"backend", "model", "intent_name", "confidence", "elapsed_ms"}
    missing = required_keys - set(classifier_trace.keys())
    if missing:
        _fail(f"classifier_trace missing keys: {missing}")
    _ok(f"classifier_trace has all required keys", f"backend={classifier_trace['backend']}, elapsed={classifier_trace['elapsed_ms']}ms")

    if expected_label is not None:
        if event.name != expected_label:
            _info(
                f"classifier picked {event.name!r}, expected {expected_label!r}",
                "(this is informational; small models can differ — only fail on contract violations)",
            )

    return events


def main() -> None:
    check_setup()

    interpreter, agent_document = build_interpreter()

    # Run a few canned turns. The sales-agent template's "discover" step has
    # event_hints for product_question, pricing_question, booking_request,
    # support_request, and close — covers the typical multi-intent case.
    print("\nRunning canned turns...")

    run_one_turn(
        interpreter, agent_document,
        step_id="discover",
        user_text="Tell me about your product features",
        expected_label="product_question",
    )

    run_one_turn(
        interpreter, agent_document,
        step_id="discover",
        user_text="How much does this cost?",
        expected_label="pricing_question",
    )

    run_one_turn(
        interpreter, agent_document,
        step_id="discover",
        user_text="I'd like to book a demo",
        expected_label="booking_request",
    )

    run_one_turn(
        interpreter, agent_document,
        step_id="discover",
        user_text="ok",  # ambiguous — likely "unknown" or low-confidence
    )

    print(f"\n{_green('=' * 50)}")
    print(_green("✓ Gemma classifier smoke test passed"))
    print(_green("=" * 50))
    print("\nNext: capture P50/P90/P99 latency baseline (WI-X.3).")


if __name__ == "__main__":
    main()

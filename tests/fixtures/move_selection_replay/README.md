# Move-Selection Replay Fixtures

This directory holds JSON fixtures for replaying recorded LLM move-selection
calls in tests, without depending on a live LLM.

## Status

P1 of doc 33's shipping plan: **scaffolding only**. The kernel records nothing
in production today and the replay mode does not yet drive real behavior.
P2+ wires the recording hook (`ConversationKernel._record_move_selection`)
into the actual LLM call site and the replay mode begins driving deterministic
test behavior.

## File format

Each fixture is one record matching `ruhu.schemas.MoveSelectionReplayRecord`:

```json
{
  "turn_id": "<unique turn identifier>",
  "input_context_hash": "<sha256 of the MoveSelectionContext>",
  "move_selection_output": {
    "selection": { ... }     // OR
    "sequence":  { ... }     // exactly one of these per record
  },
  "validation_result": {
    "outcome": "accepted",
    "committed_move_type": "answer",
    ...
  },
  "committed_deltas": {}
}
```

- `move_selection_output` follows `MoveSelectionOutput` (XOR of `selection`
  and `sequence`).
- `validation_result` follows `ValidationResult`. Optional in P1; required
  in P2+ once validation runs.
- `committed_deltas` is empty in P1. In P2+ it holds the state/fact/tool
  changes a runtime would have committed when accepting the move.

## Replay modes

The pytest fixture `move_selection_replay_mode` (defined in `tests/conftest.py`)
takes one of three values:

- `"deterministic"` — kernel never invokes LLM move selection. Default in CI.
- `"recorded"` — kernel intercepts `_select_move` and returns the recorded
  output instead of calling the LLM. P1: applying a recorded fixture still
  raises `NotImplementedError` from the stub `_select_move`, which is the
  expected wired-but-not-active state. P2+ replaces this with real replay.
- `"live"` — kernel calls the real LLM. Used only for integration tests
  with explicit credentials.

## Sample fixtures

### P1 (parser scaffolding)

- `sample_capture_proposal.json` — single-move propose_transition with email
- `sample_sequence_apologize_then_repair.json` — apologize + repair sequence

### P3 (per-move replay coverage — doc 39 WI-10)

Single-move fixtures, one per move type:

- `move_answer.json`
- `move_clarify.json`
- `move_acknowledge.json`
- `move_pause.json`
- `move_repair.json`
- `move_smalltalk_and_return.json`
- `move_ask_for_missing_info.json`
- `move_apologize.json`
- `move_thank.json`
- `move_confirm_understanding.json`

Multi-move sequences:

- `sample_sequence_apologize_then_repair.json` — apologize + repair
- `sequence_acknowledge_answer_ask.json` — ack + answer + re-ask (3-move)
- `sequence_thank_confirm_propose.json` — ack + confirm + structural commit
- `sequence_apologize_pause_proactive.json` — apologize + pause (recovery)
- `sequence_confirm_propose_transition.json` — confirm-then-commit gating

All fixtures are **synthetic** — they do not come from real LLM output.
They exist to:
1. Exercise the fixture parser and replay-mode wiring
2. Provide deterministic LLM stand-ins for E2E regression tests
3. Pin the JSON-shape contract that P3+ LLM clients must match

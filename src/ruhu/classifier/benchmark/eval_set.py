"""Eval-set loader for the Stage 2.5 benchmark harness.

An eval row is a single ``(step, user_text, gold_chosen_label)`` triple plus the
context the classifier needs to build its prompt. The harness reads JSONL —
each line is one row — because the Stage 2.5 eval sets are per-agent stratified
samples that are easy to hand-curate or generate from production traces.

Schema (one JSON object per line):

```
{
  "agent_id": "melonpay_support_demo_b720a0ec",
  "agent_version_id": "v3",
  "step_id": "entry",
  "step_name": "Entry",
  "step_summary": "Triage the user's reason for contacting MelonPay.",
  "candidate_labels": {
    "transfer_status": "User is asking about a money transfer.",
    "kyc_help": "User has a KYC / identity verification question."
  },
  "user_text": "where is my money?",
  "gold_chosen_label": "transfer_status",
  "language": "en"
}
```

``language`` is optional — when absent it defaults to ``"unknown"`` and the
language breakdown collapses into one bucket.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from ..protocol import ClassificationRequest


@dataclass(slots=True, frozen=True)
class EvalRow:
    agent_id: str
    agent_version_id: str
    step_id: str
    step_name: str
    step_summary: str
    candidate_labels: dict[str, str]
    user_text: str
    gold_chosen_label: str | None
    language: str = "unknown"

    def to_classification_request(
        self,
        *,
        lora_name: str | None = None,
    ) -> ClassificationRequest:
        return ClassificationRequest(
            agent_id=self.agent_id,
            agent_version_id=self.agent_version_id,
            step_id=self.step_id,
            step_name=self.step_name,
            step_summary=self.step_summary,
            user_text=self.user_text,
            candidate_labels=dict(self.candidate_labels),
            lora_name=lora_name,
        )


def load_eval_set(path: str | Path) -> list[EvalRow]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return list(_iter_rows(f))


def _iter_rows(stream: Iterable[str]) -> Iterator[EvalRow]:
    for line_no, raw in enumerate(stream, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"eval-set line {line_no}: invalid JSON: {exc}") from exc
        try:
            yield EvalRow(
                agent_id=str(data["agent_id"]),
                agent_version_id=str(data["agent_version_id"]),
                step_id=str(data["step_id"]),
                step_name=str(data.get("step_name", data["step_id"])),
                step_summary=str(data.get("step_summary", "")),
                candidate_labels=dict(data["candidate_labels"]),
                user_text=str(data["user_text"]),
                gold_chosen_label=(
                    str(data["gold_chosen_label"])
                    if data.get("gold_chosen_label") is not None
                    else None
                ),
                language=str(data.get("language", "unknown")),
            )
        except KeyError as exc:
            raise ValueError(
                f"eval-set line {line_no}: missing required field {exc}"
            ) from exc

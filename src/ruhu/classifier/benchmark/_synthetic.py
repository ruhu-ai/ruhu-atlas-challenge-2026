"""Synthetic eval rows + stochastic classifier for smoke testing.

Used by WI-2.5.1's smoke test (verify CSV shape on a 100-row synthetic set
with one fake backend) and by ad-hoc local runs without GPU access. Not
shipped to production — this module is import-safe in any environment.
"""
from __future__ import annotations

import math
import random

from ..protocol import ClassificationRequest, ClassificationResult
from .eval_set import EvalRow

_INTENT_CATALOG = {
    "transfer_status": "User is asking about a money transfer.",
    "kyc_help": "User has a KYC / identity verification question.",
    "card_freeze": "User wants their card frozen.",
    "fraud_or_aml": "User reports suspected fraud.",
}

_USER_TEMPLATES = {
    "transfer_status": [
        "where is my money?",
        "transfer not arrived yet",
        "still waiting for my payment",
    ],
    "kyc_help": [
        "i need to verify my id",
        "kyc rejected",
        "passport upload failed",
    ],
    "card_freeze": [
        "freeze my card",
        "lost my card need to block it",
        "stolen card help",
    ],
    "fraud_or_aml": [
        "suspicious transaction on my account",
        "i think someone hacked me",
        "fraud alert",
    ],
}


def make_synthetic_eval_set(
    *,
    n_rows: int = 100,
    languages: tuple[str, ...] = ("en", "sw"),
    seed: int = 42,
) -> list[EvalRow]:
    """Build a deterministic synthetic eval set with stratified intents."""
    rng = random.Random(seed)
    intents = list(_INTENT_CATALOG.keys())
    rows: list[EvalRow] = []
    for i in range(n_rows):
        intent = intents[i % len(intents)]
        language = languages[i % len(languages)]
        templates = _USER_TEMPLATES[intent]
        user_text = templates[rng.randrange(len(templates))]
        rows.append(
            EvalRow(
                agent_id="synthetic_agent",
                agent_version_id="v_synthetic",
                step_id="entry",
                step_name="Entry",
                step_summary="Triage the user's reason for contacting support.",
                candidate_labels=dict(_INTENT_CATALOG),
                user_text=user_text,
                gold_chosen_label=intent,
                language=language,
            )
        )
    rng.shuffle(rows)
    return rows


class StochasticFakeClassifier:
    """Returns the gold label with probability ``fidelity``, else a random other intent.

    Confidence is calibrated to fidelity so the ECE metric exercises a
    realistic distribution rather than always-1.0. ``elapsed_ms`` is sampled
    around ``mean_latency_ms`` so the latency percentiles aren't degenerate.
    """

    def __init__(
        self,
        *,
        fidelity: float = 0.85,
        mean_latency_ms: float = 50.0,
        backend: str = "synthetic",
        lora_name: str | None = None,
        seed: int = 0,
    ) -> None:
        if not 0.0 <= fidelity <= 1.0:
            raise ValueError("fidelity must be in [0, 1]")
        self._fidelity = fidelity
        self._mean_latency_ms = mean_latency_ms
        self._backend = backend
        self._lora_name = lora_name
        self._rng = random.Random(seed)

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        intents = list(request.candidate_labels.keys())
        if not intents:
            return ClassificationResult(
                chosen_label=None,
                confidence=0.0,
                backend=self._backend,
                error="empty_request",
            )
        gold_guess = self._extract_gold_from_text(request.user_text, intents)
        if self._rng.random() < self._fidelity and gold_guess is not None:
            chosen = gold_guess
            confidence = _truncated_gauss(self._rng, self._fidelity, 0.05)
        else:
            wrong_pool = [i for i in intents if i != gold_guess] or intents
            chosen = self._rng.choice(wrong_pool)
            confidence = _truncated_gauss(self._rng, 1.0 - self._fidelity, 0.10)
        return ClassificationResult(
            chosen_label=chosen,
            confidence=confidence,
            backend=self._backend,
            lora_name=self._lora_name,
            elapsed_ms=int(_truncated_gauss(self._rng, self._mean_latency_ms, 8.0, lo=1.0)),
            prefill_tokens=len(request.user_text.split()),
            decode_tokens=1,
        )

    @staticmethod
    def _extract_gold_from_text(user_text: str, intents: list[str]) -> str | None:
        text_lower = user_text.lower()
        keywords = {
            "transfer_status": ("money", "transfer", "payment", "waiting"),
            "kyc_help": ("verify", "kyc", "passport", "id"),
            "card_freeze": ("card", "freeze", "block", "stolen", "lost"),
            "fraud_or_aml": ("suspicious", "hack", "fraud"),
        }
        for intent in intents:
            for keyword in keywords.get(intent, ()):
                if keyword in text_lower:
                    return intent
        return None


def _truncated_gauss(
    rng: random.Random,
    mean: float,
    stddev: float,
    *,
    lo: float = 0.0,
    hi: float = math.inf,
) -> float:
    for _ in range(8):
        v = rng.gauss(mean, stddev)
        if lo <= v <= hi:
            return v
    return max(lo, min(hi, mean))


__all__ = [
    "StochasticFakeClassifier",
    "make_synthetic_eval_set",
]

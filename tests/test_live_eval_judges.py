"""Tests for the LLM-judge scorers in ``ruhu.live_eval_judges``.

The judge tests deliberately don't call any real LLM. Every scenario
uses ``NullLLMJudge`` or ``CallableLLMJudge(lambda: ...)`` so the suite
runs fast, deterministically, and without network. The contract under
test is the same one that production providers must satisfy:

  * Returns a ``JudgeResult`` within the configured timeout
  * Score is clamped into [0, 1]
  * Rationale is truncated and non-fatal if missing
  * Timeouts and exceptions degrade to the 0.5 sentinel without
    bringing down the worker
"""
from __future__ import annotations

import threading
import time

import pytest

from ruhu.live_eval import LiveTurnScore
from ruhu.live_eval_judges import (
    CallableLLMJudge,
    JudgeResult,
    LLMJudgeScorer,
    NullLLMJudge,
    _extract_json_object,
    _parse_judge_result,
    make_all_llm_scorers,
    make_correctness_scorer,
    make_helpfulness_scorer,
    make_safety_scorer,
)
from ruhu.schemas import (
    ActionRecord,
    NormalizedObservationRecord,
    RenderedMessage,
    TurnTrace,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_trace(
    *,
    user_text: str = "Can I get a refund?",
    agent_messages: list[str] | None = None,
    trace_id: str = "trace-1",
) -> TurnTrace:
    return TurnTrace(
        trace_id=trace_id,
        conversation_id="conv-1",
        organization_id="org-1",
        turn_id=f"turn-{trace_id}",
        agent_id="agent-1",
        step_before="discovery",
        step_after="discovery",
        chosen_action=ActionRecord(type="reply", reason="test"),
        normalized_observation=NormalizedObservationRecord(
            text_present=bool(user_text),
            redacted_text=user_text,
        ),
        emitted_messages=[
            RenderedMessage(role="assistant", text=t)
            for t in (agent_messages or ["Sure, let me help with that."])
        ],
    )


# ── NullLLMJudge ───────────────────────────────────────────────────────────────

class TestNullLLMJudge:
    def test_default_score_is_neutral(self) -> None:
        """0.5 is the deliberate "judge not configured" sentinel — see docs."""
        judge = NullLLMJudge()
        result = judge("any prompt")
        assert result.score == 0.5
        assert "no_judge" in result.rationale

    def test_custom_score_and_rationale(self) -> None:
        judge = NullLLMJudge(score=0.9, rationale="optimistic-default")
        result = judge("any prompt")
        assert result.score == 0.9
        assert result.rationale == "optimistic-default"

    def test_score_must_be_in_range(self) -> None:
        with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
            NullLLMJudge(score=1.5)
        with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
            NullLLMJudge(score=-0.1)


# ── CallableLLMJudge ──────────────────────────────────────────────────────────

class TestCallableLLMJudge:
    def test_passes_through_dict_response(self) -> None:
        judge = CallableLLMJudge(
            lambda prompt: {"score": 0.8, "rationale": "looks good"}
        )
        result = judge("test")
        assert isinstance(result, JudgeResult)
        assert result.score == 0.8
        assert result.rationale == "looks good"

    def test_clamps_out_of_range_score(self) -> None:
        """A model that hallucinates score=2.0 shouldn't crash the worker."""
        judge = CallableLLMJudge(lambda _p: {"score": 2.5, "rationale": "x"})
        result = judge("test")
        assert result.score == 1.0  # clamped

        judge_low = CallableLLMJudge(lambda _p: {"score": -0.5, "rationale": "x"})
        result_low = judge_low("test")
        assert result_low.score == 0.0  # clamped

    def test_rejects_non_dict_response(self) -> None:
        judge = CallableLLMJudge(lambda _p: "not a dict")
        with pytest.raises(TypeError, match="must return a dict"):
            judge("test")

    def test_rejects_missing_score_key(self) -> None:
        judge = CallableLLMJudge(lambda _p: {"rationale": "no score given"})
        with pytest.raises(ValueError, match="missing 'score'"):
            judge("test")

    def test_rejects_non_numeric_score(self) -> None:
        judge = CallableLLMJudge(lambda _p: {"score": "high", "rationale": "x"})
        with pytest.raises(ValueError, match="not a number"):
            judge("test")

    def test_missing_rationale_defaults_to_empty(self) -> None:
        judge = CallableLLMJudge(lambda _p: {"score": 0.7})
        result = judge("test")
        assert result.rationale == ""


# ── LLMJudgeScorer ────────────────────────────────────────────────────────────

class TestLLMJudgeScorer:
    def test_rejects_non_llm_dimension(self) -> None:
        """``goal_completion`` is not an LLM-judged dimension — it has its
        own deterministic scorer."""
        with pytest.raises(ValueError, match="does not support"):
            LLMJudgeScorer(dimension="goal_completion", judge=NullLLMJudge())  # type: ignore[arg-type]

    def test_default_name_includes_dimension(self) -> None:
        scorer = make_correctness_scorer(NullLLMJudge())
        assert scorer.name == "correctness_llm_judge"
        assert scorer.version == "v1"

    def test_explicit_name_override(self) -> None:
        scorer = make_correctness_scorer(
            NullLLMJudge(), scorer_name="custom_correctness", scorer_version="v2",
        )
        assert scorer.name == "custom_correctness"
        assert scorer.version == "v2"

    def test_score_propagates_trace_identity(self) -> None:
        scorer = make_helpfulness_scorer(NullLLMJudge())
        trace = _make_trace(trace_id="t-xyz")
        score = scorer(trace)
        assert isinstance(score, LiveTurnScore)
        assert score.trace_id == "t-xyz"
        assert score.dimension == "helpfulness"
        assert score.scorer_name == "helpfulness_llm_judge"

    def test_judge_receives_user_and_agent_text(self) -> None:
        captured: dict[str, str] = {}

        def _capture(prompt: str) -> dict:
            captured["prompt"] = prompt
            return {"score": 0.6, "rationale": "ok"}

        scorer = make_correctness_scorer(CallableLLMJudge(_capture))
        scorer(_make_trace(
            user_text="What's my balance?",
            agent_messages=["Your balance is $42.", "Anything else?"],
        ))
        assert "What's my balance?" in captured["prompt"]
        assert "Your balance is $42." in captured["prompt"]
        assert "Anything else?" in captured["prompt"]

    def test_judge_score_is_persisted(self) -> None:
        scorer = make_safety_scorer(
            CallableLLMJudge(lambda _p: {"score": 0.95, "rationale": "all clear"})
        )
        score = scorer(_make_trace())
        assert score.score == 0.95
        assert score.notes == "all clear"

    def test_long_rationale_is_truncated(self) -> None:
        long_rationale = "x" * 5000
        scorer = make_safety_scorer(
            CallableLLMJudge(lambda _p: {"score": 0.5, "rationale": long_rationale}),
            max_rationale_chars=200,
        )
        score = scorer(_make_trace())
        assert score.notes is not None
        assert len(score.notes) == 200

    def test_long_user_text_is_truncated_in_prompt(self) -> None:
        captured: dict[str, str] = {}
        scorer = make_correctness_scorer(
            CallableLLMJudge(lambda p: captured.setdefault("p", p) or {"score": 0.5, "rationale": "x"}),
            max_user_chars=50,
        )
        scorer(_make_trace(user_text="A" * 10_000))
        # The user text in the prompt should be capped at max_user_chars
        prompt = captured["p"]
        # Count the run of 'A's
        run_of_a = max((m.end() - m.start() for m in __import__("re").finditer(r"A+", prompt)), default=0)
        assert run_of_a <= 50

    def test_judge_exception_yields_sentinel_score(self) -> None:
        """A judge that raises must NOT crash the scorer — return a
        labelled neutral score so the worker can persist + log + move on."""
        def _angry(_prompt: str) -> dict:
            raise RuntimeError("provider went down")

        scorer = make_correctness_scorer(CallableLLMJudge(_angry))
        score = scorer(_make_trace())
        assert score.score == 0.5
        assert "judge_error:RuntimeError" in (score.notes or "")

    def test_judge_timeout_yields_sentinel_score(self) -> None:
        """A judge that hangs past the timeout MUST not stall the scorer."""
        slow = threading.Event()

        def _hangs(_prompt: str) -> dict:
            slow.wait(timeout=10.0)  # Will be unblocked on test teardown
            return {"score": 0.99, "rationale": "would have been great"}

        scorer = make_correctness_scorer(
            CallableLLMJudge(_hangs),
            timeout_seconds=0.5,  # short cap
        )
        start = time.monotonic()
        try:
            score = scorer(_make_trace())
        finally:
            slow.set()  # release the runaway thread (daemonised, but tidy)
        elapsed = time.monotonic() - start

        assert score.score == 0.5
        assert "judge_timeout" in (score.notes or "")
        # We must have returned within ~the timeout, not the 10s blocker
        assert elapsed < 2.0

    def test_invalid_judge_response_type_yields_sentinel(self) -> None:
        """A judge implementation that returns the wrong type (not a
        ``JudgeResult``) is treated like any other failure."""
        class _BrokenJudge:
            def __call__(self, prompt: str):
                return "not a JudgeResult"  # wrong type

        scorer = make_helpfulness_scorer(_BrokenJudge())
        score = scorer(_make_trace())
        # The exception path runs because LLMJudgeScorer expects
        # JudgeResult; "not a JudgeResult" is neither raised nor a
        # JudgeResult → falls into the "invalid type" branch.
        assert score.score == 0.5


# ── Factories ─────────────────────────────────────────────────────────────────

class TestFactories:
    def test_make_all_llm_scorers_covers_three_dimensions(self) -> None:
        scorers = make_all_llm_scorers(NullLLMJudge())
        dimensions = {s.dimension for s in scorers}
        assert dimensions == {"correctness", "helpfulness", "safety"}
        assert all(isinstance(s, LLMJudgeScorer) for s in scorers)

    def test_factory_kwargs_propagate_to_scorers(self) -> None:
        scorers = make_all_llm_scorers(
            NullLLMJudge(),
            scorer_version="v2",
            timeout_seconds=15.0,
        )
        for s in scorers:
            assert s.version == "v2"
            assert s._timeout_seconds == 15.0


# ── LiveEvalRuntime integration with LLM judge ───────────────────────────────

class TestRuntimeLLMJudgeIntegration:
    """``LiveEvalRuntime.from_settings(llm_judge=...)`` wires up all 4
    quality dimensions in one go. Without a judge, only goal_completion
    runs — which is the right default."""

    def test_no_judge_means_only_goal_completion(self) -> None:
        """Without a judge, live eval ships goal_completion only."""
        from unittest.mock import MagicMock

        from ruhu.live_eval import LiveEvalRuntime

        # Bypass the SQL store with a mock session factory — we only
        # care about scorer composition, not persistence.
        runtime = LiveEvalRuntime.from_settings(
            session_factory=MagicMock(),
            sample_rate=1.0,
        )
        names = [s.name for s in runtime._scorers]
        assert names == ["goal_completion_heuristic"]

    def test_with_judge_adds_three_llm_scorers(self) -> None:
        from unittest.mock import MagicMock

        from ruhu.live_eval import LiveEvalRuntime

        runtime = LiveEvalRuntime.from_settings(
            session_factory=MagicMock(),
            sample_rate=1.0,
            llm_judge=NullLLMJudge(),
        )
        names = [s.name for s in runtime._scorers]
        assert names == [
            "goal_completion_heuristic",
            "correctness_llm_judge",
            "helpfulness_llm_judge",
            "safety_llm_judge",
        ]

    def test_explicit_scorers_overrides_judge(self) -> None:
        """``scorers=...`` takes precedence — useful in tests + custom setups."""
        from unittest.mock import MagicMock

        from ruhu.live_eval import GoalCompletionScorer, LiveEvalRuntime

        runtime = LiveEvalRuntime.from_settings(
            session_factory=MagicMock(),
            sample_rate=1.0,
            scorers=[GoalCompletionScorer()],
            llm_judge=NullLLMJudge(),  # ignored when scorers is explicit
        )
        names = [s.name for s in runtime._scorers]
        assert names == ["goal_completion_heuristic"]


# ── Cost accounting (token counters) ──────────────────────────────────────────

class TestCostAccounting:
    """Judges that report token counts must drive Prometheus counters.

    Tests read counter samples directly from the registry — gives a real
    integration check (label set, increment correctness) rather than
    mocking the metric.
    """

    def _counter_sum(self, counter, **labels) -> float:
        for metric in counter.collect():
            for sample in metric.samples:
                if sample.name.endswith("_total") and all(
                    sample.labels.get(k) == v for k, v in labels.items()
                ):
                    return sample.value
        return 0.0

    def _make_trace(self) -> TurnTrace:
        return _make_trace(user_text="test", agent_messages=["reply"])

    def test_judge_with_no_token_data_emits_no_cost_counters(self) -> None:
        """``NullLLMJudge`` doesn't report tokens — counters stay at 0
        for that scorer's labels."""
        from ruhu.observability.metrics import live_eval_judge_tokens_total

        scorer = make_correctness_scorer(
            NullLLMJudge(), scorer_name="cost_test_null",
        )
        before = self._counter_sum(
            live_eval_judge_tokens_total,
            scorer="cost_test_null", direction="input",
        )
        scorer(self._make_trace())
        after = self._counter_sum(
            live_eval_judge_tokens_total,
            scorer="cost_test_null", direction="input",
        )
        assert after == before  # no change — judge didn't report

    def test_judge_with_token_data_drives_counters(self) -> None:
        from ruhu.observability.metrics import (
            live_eval_judge_cost_usd_total,
            live_eval_judge_tokens_total,
        )

        scorer = make_correctness_scorer(
            CallableLLMJudge(lambda _p: {
                "score": 0.8,
                "rationale": "ok",
                "input_tokens": 250,
                "output_tokens": 30,
                "cost_usd": 0.001,
            }),
            scorer_name="cost_test_real",
        )
        before_in = self._counter_sum(
            live_eval_judge_tokens_total,
            scorer="cost_test_real", direction="input",
        )
        before_out = self._counter_sum(
            live_eval_judge_tokens_total,
            scorer="cost_test_real", direction="output",
        )
        before_cost = self._counter_sum(
            live_eval_judge_cost_usd_total, scorer="cost_test_real",
        )
        scorer(self._make_trace())
        scorer(self._make_trace())  # 2 calls
        assert self._counter_sum(
            live_eval_judge_tokens_total,
            scorer="cost_test_real", direction="input",
        ) == before_in + 500  # 250 × 2
        assert self._counter_sum(
            live_eval_judge_tokens_total,
            scorer="cost_test_real", direction="output",
        ) == before_out + 60   # 30 × 2
        assert self._counter_sum(
            live_eval_judge_cost_usd_total, scorer="cost_test_real",
        ) == pytest.approx(before_cost + 0.002)

    def test_token_counter_skipped_on_judge_failure(self) -> None:
        """A judge that raises produces a sentinel score and NO cost
        counters — the failed call had zero token spend."""
        from ruhu.observability.metrics import live_eval_judge_tokens_total

        scorer = make_correctness_scorer(
            CallableLLMJudge(lambda _p: (_ for _ in ()).throw(RuntimeError("api down"))),
            scorer_name="cost_test_failure",
        )
        before = self._counter_sum(
            live_eval_judge_tokens_total,
            scorer="cost_test_failure", direction="input",
        )
        score = scorer(self._make_trace())
        assert score.score == 0.5  # sentinel
        after = self._counter_sum(
            live_eval_judge_tokens_total,
            scorer="cost_test_failure", direction="input",
        )
        assert after == before  # never charged

    def test_partial_token_data_records_what_is_present(self) -> None:
        """A judge that reports tokens but no cost gets token counters
        only — no zero-cost spurious entries."""
        from ruhu.observability.metrics import (
            live_eval_judge_cost_usd_total,
            live_eval_judge_tokens_total,
        )

        scorer = make_safety_scorer(
            CallableLLMJudge(lambda _p: {
                "score": 0.5,
                "rationale": "x",
                "input_tokens": 100,
                # output_tokens / cost_usd absent
            }),
            scorer_name="cost_test_partial",
        )
        before_cost = self._counter_sum(
            live_eval_judge_cost_usd_total, scorer="cost_test_partial",
        )
        scorer(self._make_trace())
        # Input recorded, cost_usd untouched (None coerces to "no observation")
        assert self._counter_sum(
            live_eval_judge_tokens_total,
            scorer="cost_test_partial", direction="input",
        ) >= 100
        assert self._counter_sum(
            live_eval_judge_cost_usd_total, scorer="cost_test_partial",
        ) == before_cost


# ── _parse_judge_result with cost fields ──────────────────────────────────────

class TestParseJudgeResultCostFields:
    def test_all_cost_fields_parsed(self) -> None:
        from ruhu.live_eval_judges import _parse_judge_result

        result = _parse_judge_result({
            "score": 0.7,
            "rationale": "x",
            "input_tokens": 150,
            "output_tokens": 25,
            "cost_usd": 0.0008,
        })
        assert result.input_tokens == 150
        assert result.output_tokens == 25
        assert result.cost_usd == 0.0008

    def test_string_token_counts_coerced(self) -> None:
        """Some judges return numeric fields as JSON strings — coerce."""
        from ruhu.live_eval_judges import _parse_judge_result

        result = _parse_judge_result({
            "score": 0.5, "rationale": "x",
            "input_tokens": "120", "output_tokens": "10",
        })
        assert result.input_tokens == 120
        assert result.output_tokens == 10

    def test_unparseable_token_field_defaults_to_none(self) -> None:
        """Garbage in token fields shouldn't take down the scorer."""
        from ruhu.live_eval_judges import _parse_judge_result

        result = _parse_judge_result({
            "score": 0.5, "rationale": "x",
            "input_tokens": "lots and lots",
            "cost_usd": "free!",
        })
        assert result.input_tokens is None
        assert result.cost_usd is None


# ── Tier resolver ─────────────────────────────────────────────────────────────

class TestBillingTierResolver:
    """``make_billing_tier_resolver`` adapts a ``BillingStore`` into the
    callable shape ``LiveEvalRuntime`` expects for tier-aware sampling."""

    def _make_store(self, *, plan_slug: str | None = "starter"):
        """Tiny stub that mimics the ``BillingStore`` Protocol shape."""
        from unittest.mock import MagicMock

        store = MagicMock()
        if plan_slug is None:
            store.get_active_subscription.return_value = None
        else:
            sub = MagicMock()
            sub.plan_id = "plan_" + plan_slug
            store.get_active_subscription.return_value = sub
            plan = MagicMock()
            plan.slug = plan_slug
            store.get_plan.return_value = plan
        return store

    def test_returns_plan_slug_for_known_org(self) -> None:
        from ruhu.live_eval import make_billing_tier_resolver

        resolve = make_billing_tier_resolver(self._make_store(plan_slug="enterprise"))
        assert resolve("org-1") == "enterprise"

    def test_returns_none_when_no_active_subscription(self) -> None:
        from ruhu.live_eval import make_billing_tier_resolver

        resolve = make_billing_tier_resolver(self._make_store(plan_slug=None))
        assert resolve("org-1") is None

    def test_returns_none_for_empty_org_id(self) -> None:
        from ruhu.live_eval import make_billing_tier_resolver

        resolve = make_billing_tier_resolver(self._make_store())
        assert resolve(None) is None
        assert resolve("") is None

    def test_swallows_billing_store_errors(self) -> None:
        """A billing-store outage MUST NOT block sampling decisions."""
        from unittest.mock import MagicMock

        from ruhu.live_eval import make_billing_tier_resolver

        store = MagicMock()
        store.get_active_subscription.side_effect = RuntimeError("DB down")
        resolve = make_billing_tier_resolver(store)
        assert resolve("org-1") is None  # not a raise


# ── Per-tier sample rate end-to-end ───────────────────────────────────────────

class TestPerTierSampleRateEndToEnd:
    """``from_settings(per_tier_rate=..., billing_store=...)`` produces a
    runtime where sampling responds to each org's tier."""

    def test_enterprise_opt_out_via_per_tier_rate(self) -> None:
        from unittest.mock import MagicMock

        from ruhu.live_eval import (
            InMemoryLiveScoreStore, LiveEvalRuntime, make_billing_tier_resolver,
        )

        # Enterprise org with rate=0; everyone else samples at 1.0.
        store = MagicMock()
        sub = MagicMock(); sub.plan_id = "plan_enterprise"
        plan = MagicMock(); plan.slug = "enterprise"
        store.get_active_subscription.return_value = sub
        store.get_plan.return_value = plan

        # Bypass SQL store — use in-memory + manual wiring because
        # from_settings constructs a real SQLAlchemy store internally.
        runtime = LiveEvalRuntime(
            store=InMemoryLiveScoreStore(),
            sampling_policy=__import__("ruhu.live_eval", fromlist=["SamplingPolicy"]).SamplingPolicy(
                default_rate=1.0,
                per_tier_rate={"enterprise": 0.0},
            ),
            tier_resolver=make_billing_tier_resolver(store),
        )

        accepted = runtime.submit(_make_trace())
        assert accepted is False  # tier=enterprise → rate=0 → rejected


# ── JSON parsing helpers ──────────────────────────────────────────────────────

class TestJsonHelpers:
    def test_extract_json_object_from_clean_response(self) -> None:
        text = '{"score": 0.7, "rationale": "ok"}'
        assert _extract_json_object(text) == text

    def test_extract_json_object_strips_chatter(self) -> None:
        text = 'Sure! Here is the result: {"score": 0.4, "rationale": "meh"}\nLet me know.'
        result = _extract_json_object(text)
        assert result is not None
        assert '"score": 0.4' in result

    def test_extract_json_object_returns_none_when_absent(self) -> None:
        assert _extract_json_object("no json here") is None
        assert _extract_json_object("") is None

    def test_parse_clamps_score(self) -> None:
        result = _parse_judge_result({"score": 1.7, "rationale": "x"})
        assert result.score == 1.0
        result_neg = _parse_judge_result({"score": -3.0, "rationale": "x"})
        assert result_neg.score == 0.0

    def test_parse_strips_rationale_whitespace(self) -> None:
        result = _parse_judge_result({"score": 0.5, "rationale": "  yes  \n"})
        assert result.rationale == "yes"

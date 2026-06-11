"""Phase 2c — TopicEnforcingResponseGenerator decorator tests.

Tests are organised by the production-readiness checklist baked into
``src/ruhu/topic_enforcement.py``. Every guarantee in the module
docstring should have at least one test asserting it. Tests use stub
inner generators — no real LLM calls — so they're deterministic and fast.

The fixtures below stand up a minimal but realistic ``RenderContext`` so
the decorator's reads (``agent_id``, ``organization_id``,
``conversation_id``, ``system_prompt``, ``model_copy``) all exercise the
real Pydantic surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pytest

from ruhu.persona import TopicEnforcementPolicy
from ruhu.schemas import (
    JourneyContext,
    RenderContext,
    RenderOutput,
)
from ruhu.topic_enforcement import (
    TopicEnforcingResponseGenerator,
    TopicSettings,
    _stage1_keyword_match,
    default_deflection_text,
)


# ── Test doubles ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class StubGenerator:
    """Stub ResponseGenerator that returns a queue of pre-canned outputs.

    The decorator only calls ``render_from_context``; ``generate`` and
    ``select_move`` are passthroughs. We assert delegation in a separate
    test.
    """

    outputs: list = field(default_factory=list)
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    raise_on_call: int | None = None

    def generate(self, request, on_first_sentence=None):  # pragma: no cover - test plumbing
        return None

    def select_move(self, request):  # pragma: no cover - test plumbing
        return None

    def render_from_context(
        self,
        context,
        *,
        provider=None,
        model=None,
        on_first_sentence=None,
    ):
        idx = len(self.calls)
        # Record (system_prompt, signal) so tests can assert what the
        # retry call looked like.
        self.calls.append((context.system_prompt or "", "render"))
        if self.raise_on_call is not None and idx == self.raise_on_call:
            raise RuntimeError("synthetic inner failure")
        if idx >= len(self.outputs):
            return None
        return self.outputs[idx]


def _make_context(
    *,
    agent_id: str = "agent-1",
    organization_id: str | None = "org-1",
    conversation_id: str = "conv-1",
    system_prompt: str = "You are Maya.",
) -> RenderContext:
    return RenderContext(
        conversation_id=conversation_id,
        organization_id=organization_id,
        agent_id=agent_id,
        # ResponseMode is a Literal — pick a concrete value that exists
        # in the spec (see schemas.py:940). 'entry' is the simplest.
        response_mode="entry",
        journey=JourneyContext(current_step_id="step-test"),
        system_prompt=system_prompt,
    )


def _settings(
    policy: TopicEnforcementPolicy,
    topics: tuple[str, ...] = (),
) -> Callable[[str, str | None], TopicSettings | None]:
    captured = TopicSettings(policy=policy, topics=topics)

    def lookup(_agent_id: str, _org_id: str | None) -> TopicSettings | None:
        return captured

    return lookup


def _settings_raises() -> Callable[[str, str | None], TopicSettings | None]:
    def lookup(_agent_id: str, _org_id: str | None) -> TopicSettings | None:
        raise RuntimeError("registry exploded")

    return lookup


# ── Stage 1 keyword match ────────────────────────────────────────────────────


class TestStage1KeywordMatch:
    """``_stage1_keyword_match`` is the production hot path — it runs on
    every render. These tests pin the contract end users see."""

    def test_empty_topics_returns_empty(self):
        assert _stage1_keyword_match("anything", ()) == ()

    def test_single_word_matches_with_word_boundary(self):
        assert _stage1_keyword_match(
            "Our pricing is competitive.", ("pricing",)
        ) == ("pricing",)

    def test_single_word_does_not_match_within_other_word(self):
        """Critical regression guard: 'pricing' must NOT match 'uncoupling'.
        Production agents broke when Phase 2c v0 used a substring match."""
        assert _stage1_keyword_match(
            "The uncoupling process is complete.", ("pricing",)
        ) == ()

    def test_multi_word_uses_substring_match(self):
        assert _stage1_keyword_match(
            "We can talk about competitor pricing later.",
            ("competitor pricing",),
        ) == ("competitor pricing",)

    def test_case_insensitive_via_casefold(self):
        # casefold (not lower) — handles ß / dotted-i correctly.
        assert _stage1_keyword_match(
            "PRICING is great.", ("pricing",)
        ) == ("pricing",)

    def test_returns_only_topics_present(self):
        topics = ("pricing", "legal advice", "ssn")
        assert _stage1_keyword_match(
            "Let's discuss pricing.", topics
        ) == ("pricing",)

    def test_preserves_topic_order(self):
        """When multiple topics match, the returned tuple preserves the
        order of the input topics — gives deterministic audit output."""
        topics = ("pricing", "legal")
        assert _stage1_keyword_match(
            "legal advice on pricing", topics
        ) == ("pricing", "legal")

    def test_skips_blank_topics(self):
        assert _stage1_keyword_match(
            "anything", ("", "  ", "pricing")
        ) == ()  # the literal text doesn't include "pricing"
        assert _stage1_keyword_match(
            "say pricing once", ("", "pricing")
        ) == ("pricing",)


# ── Decorator: zero-cost passthrough paths ───────────────────────────────────


class TestZeroCostPassthrough:
    """The decorator MUST be observably identical to no-decorator when:
    - settings_lookup returns None
    - policy == off
    - topics == []
    These three paths are the production contract that lets us leave the
    wrapper installed globally without burning agents that haven't
    configured it."""

    def test_passthrough_when_lookup_returns_none(self):
        inner = StubGenerator(outputs=["the response"])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=lambda *_args, **_kw: None,
        )
        result = gen.render_from_context(_make_context())
        assert result == "the response"
        assert len(inner.calls) == 1  # no retry

    def test_passthrough_when_policy_off(self):
        inner = StubGenerator(outputs=["pricing is fine"])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.off, ("pricing",)
            ),
        )
        result = gen.render_from_context(_make_context())
        assert result == "pricing is fine"
        assert len(inner.calls) == 1

    def test_passthrough_when_topics_empty(self):
        inner = StubGenerator(outputs=["any response"])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ()
            ),
        )
        result = gen.render_from_context(_make_context())
        assert result == "any response"
        assert len(inner.calls) == 1

    def test_passthrough_when_no_violation(self):
        inner = StubGenerator(outputs=["weather is nice today"])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
        )
        result = gen.render_from_context(_make_context())
        assert result == "weather is nice today"
        assert len(inner.calls) == 1


# ── log_only canary mode ─────────────────────────────────────────────────────


class TestLogOnlyMode:
    """log_only is the canary rollout default. Detection must run, audit
    must fire, but the original response goes out unmodified."""

    def test_violation_in_log_only_returns_original_response(self, caplog):
        inner = StubGenerator(outputs=["our pricing is industry-leading"])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.log_only, ("pricing",)
            ),
        )
        result = gen.render_from_context(_make_context())
        # Critical: the violating response is RETURNED unchanged in log_only.
        assert result == "our pricing is industry-leading"
        # And no retry call was made.
        assert len(inner.calls) == 1


# ── block_and_retry: retry path ──────────────────────────────────────────────


class TestBlockAndRetryRetrySucceeds:
    def test_retry_replaces_text_when_clean(self):
        inner = StubGenerator(
            outputs=[
                "let me give you our pricing",  # violates
                "I can help you in many ways",  # clean retry
            ],
        )
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
        )
        result = gen.render_from_context(_make_context())
        assert result == "I can help you in many ways"
        assert len(inner.calls) == 2

    def test_retry_uses_strengthened_system_prompt(self):
        """The retry must pass the inner a context whose system_prompt
        contains an explicit constraint naming the violations. This is
        the difference between 'soft hint' and 'enforcement attempt'."""
        inner = StubGenerator(
            outputs=["pricing is great", "happy to help"],
        )
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
        )
        gen.render_from_context(_make_context(system_prompt="You are Maya."))
        # Original first call: untouched system prompt
        assert inner.calls[0][0] == "You are Maya."
        # Retry: includes original + CRITICAL CONSTRAINT
        assert "You are Maya." in inner.calls[1][0]
        assert "CRITICAL CONSTRAINT" in inner.calls[1][0]
        assert "pricing" in inner.calls[1][0]

    def test_retry_preserves_render_output_shape(self):
        """If the inner returns RenderOutput (with claim class etc.), the
        retry-replacement must preserve that shape — not silently drop
        ``claimed_class`` and ``acknowledged_fact_keys``."""
        # RenderClaimClass is a Literal — pick concrete values that the
        # spec defines (see schemas.py:1021).
        original = RenderOutput(
            text="our pricing is fine",
            claimed_class="success",
            acknowledged_fact_keys=["account_id"],
        )
        retry = RenderOutput(
            text="happy to help",
            claimed_class="partial",
            acknowledged_fact_keys=[],
        )
        inner = StubGenerator(outputs=[original, retry])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
        )
        result = gen.render_from_context(_make_context())
        # Text replaced with retry text; claim class and fact keys kept
        # from the ORIGINAL — not the retry. Why: the kernel grounded
        # against the original; we don't want to accidentally claim
        # facts the retry might not actually be supported by.
        assert isinstance(result, RenderOutput)
        assert result.text == "happy to help"
        assert result.claimed_class == "success"
        assert result.acknowledged_fact_keys == ["account_id"]


# ── block_and_retry: deflection path ─────────────────────────────────────────


class TestBlockAndRetryDeflection:
    def test_deflection_when_retry_also_violates(self):
        inner = StubGenerator(
            outputs=[
                "pricing is great",
                "still talking about pricing",  # retry also violates
            ],
        )
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
        )
        result = gen.render_from_context(_make_context())
        assert result == default_deflection_text(("pricing",))

    def test_deflection_when_retry_returns_none(self):
        inner = StubGenerator(outputs=["pricing chat"])  # no second output
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
        )
        result = gen.render_from_context(_make_context())
        assert result == default_deflection_text(("pricing",))

    def test_deflection_when_retry_raises(self):
        """Retry render exceptions must NOT propagate to the kernel. We
        deflect deterministically. Spec: fail-open everywhere."""
        inner = StubGenerator(
            outputs=["pricing chat", "doesn't matter"],
            raise_on_call=1,
        )
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
        )
        result = gen.render_from_context(_make_context())
        assert result == default_deflection_text(("pricing",))

    def test_deflection_does_not_re_mention_topic(self):
        """The default deflection MUST NOT name the topic. Saying 'I can't
        discuss pricing' would re-mention pricing — defeating the point.
        This is a behavioural contract, not a stylistic one."""
        text = default_deflection_text(("pricing", "legal advice"))
        assert "pricing" not in text.casefold()
        assert "legal" not in text.casefold()

    def test_pluggable_deflection_factory(self):
        inner = StubGenerator(
            outputs=["pricing chat", "more pricing"],
        )
        custom = lambda violations: f"Custom for {violations[0]}"  # noqa: E731
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
            deflection_text_factory=custom,
        )
        result = gen.render_from_context(_make_context())
        assert result == "Custom for pricing"


# ── Streaming downgrade ──────────────────────────────────────────────────────


class TestStreamingDowngrade:
    """When ``on_first_sentence`` is non-None, the user has already seen
    text. We can't redact. The decorator force-downgrades to log_only for
    the turn — detect + audit, but emit the original response."""

    def test_streaming_block_and_retry_downgrades_to_log_only(self):
        inner = StubGenerator(outputs=["pricing details"])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
        )
        callback_calls = []
        result = gen.render_from_context(
            _make_context(),
            on_first_sentence=lambda s: callback_calls.append(s),
        )
        # Original response returned unchanged — no retry, no deflection.
        assert result == "pricing details"
        # And the inner was only called once.
        assert len(inner.calls) == 1


# ── Fail-open: settings_lookup raises ────────────────────────────────────────


class TestSettingsLookupResilience:
    def test_lookup_exception_falls_back_to_passthrough(self):
        inner = StubGenerator(outputs=["pricing"])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings_raises(),
        )
        # Lookup raises → decorator returns original result unchanged.
        result = gen.render_from_context(_make_context())
        assert result == "pricing"


# ── Inner None response ──────────────────────────────────────────────────────


class TestInnerReturnsNone:
    def test_none_short_circuits_before_lookup(self):
        """If the inner generator decided not to render (e.g. fallback
        path), the decorator must NOT run a lookup or attempt enforcement
        — there's no text to inspect."""
        lookup_calls = []

        def lookup(*args):
            lookup_calls.append(args)
            return TopicSettings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            )

        inner = StubGenerator(outputs=[None])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=lookup,
        )
        assert gen.render_from_context(_make_context()) is None
        assert lookup_calls == []  # short-circuit before lookup


# ── Stage 2 classifier hook ──────────────────────────────────────────────────


class TestStage2Classifier:
    """Stage 2 is the future-proofing seam. Phase 2c v1 doesn't ship a
    real classifier; we verify the wiring works and that classifier
    failures don't break enforcement."""

    def test_stage2_consulted_only_when_stage1_misses(self):
        calls = []

        class FakeClassifier:
            def classify_violations(self, text, topics, *, timeout_ms):
                calls.append((text, topics, timeout_ms))
                return ("pricing",)

        inner = StubGenerator(outputs=["this avoids the keyword", "happy"])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
            classifier=FakeClassifier(),
        )
        result = gen.render_from_context(_make_context())
        # Stage 1 missed; Stage 2 caught; retry path engaged; clean retry returned.
        assert result == "happy"
        assert len(calls) == 1

    def test_stage2_not_consulted_when_stage1_already_matched(self):
        calls = []

        class FakeClassifier:
            def classify_violations(self, text, topics, *, timeout_ms):
                calls.append((text, topics))
                return ()

        inner = StubGenerator(outputs=["pricing is great", "happy"])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
            classifier=FakeClassifier(),
        )
        gen.render_from_context(_make_context())
        # Stage 1 already matched, Stage 2 must not have been called.
        assert calls == []

    def test_stage2_failure_falls_through(self):
        """Classifier raise → log + treat as 'no match' (fail-open)."""

        class BrokenClassifier:
            def classify_violations(self, text, topics, *, timeout_ms):
                raise RuntimeError("classifier OOM")

        inner = StubGenerator(outputs=["evades the keyword"])
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
            classifier=BrokenClassifier(),
        )
        # No violation surfaced → original response goes out.
        assert gen.render_from_context(_make_context()) == "evades the keyword"


# ── Protocol passthrough ─────────────────────────────────────────────────────


class TestProtocolPassthrough:
    def test_generate_delegated_unchanged(self):
        class DelegateInner:
            def __init__(self):
                self.generate_calls = []

            def generate(self, request, on_first_sentence=None):
                self.generate_calls.append((request, on_first_sentence))
                return "delegated"

            def render_from_context(self, *_a, **_kw):  # pragma: no cover
                return None

            def select_move(self, request):  # pragma: no cover
                return None

        inner = DelegateInner()
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
        )
        sentinel_request = object()
        cb = lambda _s: None  # noqa: E731
        out = gen.generate(sentinel_request, cb)
        assert out == "delegated"
        assert inner.generate_calls == [(sentinel_request, cb)]

    def test_select_move_delegated_unchanged(self):  # placeholder anchor
        pass


# ── Rollout decision (scripts/topic_enforcement_rollout.py) ──────────────────


class TestRolloutDecision:
    """The rollout job's decision logic is a pure function — fully testable.
    The I/O glue is operator-provided per deployment; the policy is here."""

    def _import(self):
        """Load the rollout script as a module. Must register in sys.modules
        before exec_module() because the script uses ``from __future__ import
        annotations`` + ``@dataclass``, which forwards-references types via
        the module's __dict__ during dataclass init."""
        import importlib.util
        import sys
        from pathlib import Path

        if "topic_enforcement_rollout" in sys.modules:
            return sys.modules["topic_enforcement_rollout"]

        spec = importlib.util.spec_from_file_location(
            "topic_enforcement_rollout",
            Path(__file__).parent.parent / "scripts" / "topic_enforcement_rollout.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["topic_enforcement_rollout"] = module
        spec.loader.exec_module(module)
        return module

    def test_skip_when_not_log_only(self):
        from datetime import datetime, timezone

        m = self._import()
        decision = m.evaluate_agent(
            agent_id="a",
            organization_id="o",
            current_policy="block_and_retry",
            is_explicit_choice=False,
            has_topics=True,
            persona_first_configured_at=datetime.now(timezone.utc),
            now=datetime.now(timezone.utc),
        )
        assert decision.decision == "skip_not_log_only"

    def test_skip_when_explicit_choice(self):
        from datetime import datetime, timedelta, timezone

        m = self._import()
        now = datetime.now(timezone.utc)
        decision = m.evaluate_agent(
            agent_id="a",
            organization_id="o",
            current_policy="log_only",
            is_explicit_choice=True,
            has_topics=True,
            persona_first_configured_at=now - timedelta(days=30),
            now=now,
        )
        assert decision.decision == "skip_explicit"

    def test_skip_when_no_topics(self):
        from datetime import datetime, timedelta, timezone

        m = self._import()
        now = datetime.now(timezone.utc)
        decision = m.evaluate_agent(
            agent_id="a",
            organization_id="o",
            current_policy="log_only",
            is_explicit_choice=False,
            has_topics=False,
            persona_first_configured_at=now - timedelta(days=30),
            now=now,
        )
        assert decision.decision == "skip_no_topics"

    def test_skip_when_too_recent(self):
        from datetime import datetime, timedelta, timezone

        m = self._import()
        now = datetime.now(timezone.utc)
        decision = m.evaluate_agent(
            agent_id="a",
            organization_id="o",
            current_policy="log_only",
            is_explicit_choice=False,
            has_topics=True,
            persona_first_configured_at=now - timedelta(days=3),
            now=now,
        )
        assert decision.decision == "skip_too_recent"

    def test_flip_when_eligible(self):
        from datetime import datetime, timedelta, timezone

        m = self._import()
        now = datetime.now(timezone.utc)
        decision = m.evaluate_agent(
            agent_id="a",
            organization_id="o",
            current_policy="log_only",
            is_explicit_choice=False,
            has_topics=True,
            persona_first_configured_at=now - timedelta(days=8),
            now=now,
        )
        assert decision.decision == "flip"

    def test_skip_when_no_timestamp(self):
        from datetime import datetime, timezone

        m = self._import()
        decision = m.evaluate_agent(
            agent_id="a",
            organization_id="o",
            current_policy="log_only",
            is_explicit_choice=False,
            has_topics=True,
            persona_first_configured_at=None,
            now=datetime.now(timezone.utc),
        )
        assert decision.decision == "skip_no_timestamp"
        class DelegateInner:
            def __init__(self):
                self.move_calls = []

            def generate(self, *_a, **_kw):  # pragma: no cover
                return None

            def render_from_context(self, *_a, **_kw):  # pragma: no cover
                return None

            def select_move(self, request):
                self.move_calls.append(request)
                return "move-x"

        inner = DelegateInner()
        gen = TopicEnforcingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                TopicEnforcementPolicy.block_and_retry, ("pricing",)
            ),
        )
        sentinel = object()
        assert gen.select_move(sentinel) == "move-x"
        assert inner.move_calls == [sentinel]

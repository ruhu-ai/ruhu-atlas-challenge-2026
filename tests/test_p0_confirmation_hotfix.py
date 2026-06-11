"""P0 confirmation-intent classifier regression tests.

These tests pin the deterministic behavior of
``ConversationKernel._classify_confirmation_intent`` against the P0 hotfix
from spec 28: cooperative-stall language ("hold on", "wait") must not be
classified as cancellation, while explicit cancel language still cancels.

WI-7 (doc 36) requires these to be marked ``regression_p0`` so a failure is
visible in CI and blocks the build.
"""

import pytest

from ruhu.kernel import ConversationKernel


@pytest.mark.regression_p0
class TestP0CancelHotfix:
    """Pin the confirmation classifier against P0 regressions."""

    @pytest.fixture()
    def kernel(self) -> ConversationKernel:
        return ConversationKernel()

    # ── cooperative stalls must NOT cancel ───────────────────────────────

    def test_hold_on_alone_is_not_cancel(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("hold on") == "unclear"

    def test_wait_alone_is_not_cancel(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("wait") == "unclear"

    def test_hold_on_with_intent_to_share_is_not_cancel(
        self, kernel: ConversationKernel
    ) -> None:
        # Replays the P0 transcript: user says they will share the requested value
        assert (
            kernel._classify_confirmation_intent("hold on, I'll share my email now")
            == "unclear"
        )

    def test_one_second_is_not_cancel(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("one second") == "unclear"

    def test_just_a_moment_is_not_cancel(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("just a moment") == "unclear"

    # ── explicit cancel language MUST cancel ─────────────────────────────

    def test_cancel_alone_is_cancel(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("cancel") == "cancel"

    def test_stop_alone_is_cancel(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("stop") == "cancel"

    def test_no_alone_is_cancel(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("no") == "cancel"

    def test_never_mind_is_cancel(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("never mind") == "cancel"

    # ── compound phrases: cancel intent dominates ────────────────────────

    def test_hold_on_cancel_compound_is_cancel(
        self, kernel: ConversationKernel
    ) -> None:
        # Cooperative stall combined with explicit cancel must still cancel.
        assert kernel._classify_confirmation_intent("hold on, cancel that") == "cancel"

    def test_wait_never_mind_is_cancel(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("wait, never mind") == "cancel"

    def test_wait_never_mind_cancel_that_is_cancel(
        self, kernel: ConversationKernel
    ) -> None:
        assert (
            kernel._classify_confirmation_intent("wait, never mind, cancel that")
            == "cancel"
        )

    # ── confirmation language still classifies as confirm ────────────────

    def test_yes_alone_is_confirm(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("yes") == "confirm"

    def test_okay_alone_is_confirm(self, kernel: ConversationKernel) -> None:
        assert kernel._classify_confirmation_intent("okay") == "confirm"


# ── Module-level shape preserved for backward-compat with the originals ──


def test_p0_confirmation_hotfix_transcript_replay_hold_on_is_not_cancel() -> None:
    """Replay the narrow P0 transcript class: hold-on language must not cancel."""
    kernel = ConversationKernel()
    assert kernel._classify_confirmation_intent("hold on") == "unclear"
    assert (
        kernel._classify_confirmation_intent("hold on, I'll share my email now")
        == "unclear"
    )


def test_p0_confirmation_hotfix_explicit_cancel_compounds_still_cancel() -> None:
    kernel = ConversationKernel()
    assert kernel._classify_confirmation_intent("hold on, cancel that") == "cancel"
    assert kernel._classify_confirmation_intent("wait, never mind") == "cancel"

"""Test keyword interpreters.

Keyword-based ``SemanticInterpreter`` implementations used by tests that
exercise intent routing by interpreter name.  Production ships LLM-based
interpreters — these exist purely for deterministic test cases.
"""
from __future__ import annotations

from ruhu.heuristics import KeywordInterpreter


def sales_interpreter() -> KeywordInterpreter:
    booking_keywords = ("demo", "discovery call", "book a call", "schedule a call")
    return KeywordInterpreter(
        rules={
            "demo_request": booking_keywords,
            "booking_request": booking_keywords,
            "support_request": ("support", "issue", "problem", "account help"),
            "pricing_question": ("price", "pricing", "cost", "plan"),
            "integration_question": ("integration", "integrations", "integrate", "tools", "webhook", "api"),
            "product_question": (
                "product",
                "feature",
                "platform",
                "workflow",
                "what does",
                "what is ruhu",
                "describe ruhu",
                "explain ruhu",
                "learn about ruhu",
                "about ruhu",
            ),
            "close": ("bye", "goodbye", "thank you", "thanks"),
        }
    )


def support_triage_interpreter() -> KeywordInterpreter:
    return KeywordInterpreter(
        rules={
            "support_request": ("support", "issue", "problem", "account help", "billing", "technical"),
            "close": ("bye", "goodbye", "thank you", "thanks"),
        }
    )

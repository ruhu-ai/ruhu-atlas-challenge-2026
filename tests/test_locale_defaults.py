"""Phase 2b — locale defaults integrity tests.

The locale defaults dictionary is an authored knowledge artifact —
not generated, not auto-translated. These tests guard the contract
that every entry stays well-formed and curated as new locales are
added or existing ones are re-reviewed.
"""
from __future__ import annotations

from datetime import date

import pytest

from ruhu.locale_defaults import (
    LOCALE_DEFAULTS,
    LOCALE_MAINTAINERS,
    LocaleDefaults,
    get_locale_defaults,
    list_supported_locales,
)


class TestCuratedShape:
    def test_dictionary_key_matches_entry_locale_code(self):
        """A copy-paste error where the dict key and the entry's
        ``locale_code`` field disagree would silently route the wrong
        greeting at composition time. Lock the invariant."""
        for key, entry in LOCALE_DEFAULTS.items():
            assert (
                entry.locale_code == key
            ), f"locale_code/key mismatch for {key!r} → {entry.locale_code!r}"

    def test_all_keys_are_bcp47_lang_region(self):
        """Schema validation on the agent side enforces BCP-47
        lang-region (``en-US``) format. The locale dictionary must
        match so a tenant's ``locale_code`` always finds an entry."""
        for key in LOCALE_DEFAULTS:
            parts = key.split("-")
            assert (
                len(parts) == 2
            ), f"{key!r} is not lang-region shape; locale_defaults keys must be BCP-47 strict"
            assert parts[0].islower()
            assert parts[1].isupper()

    def test_no_format_string_placeholders(self):
        """All template strings use ``string.Template`` syntax
        (``$persona_name``), not f-string / str.format syntax. A
        ``{persona_name}`` would render as literal in the prompt."""
        for key, entry in LOCALE_DEFAULTS.items():
            for field_name in ("greeting", "signoff", "ramadan_greeting", "christmas_greeting"):
                value = getattr(entry, field_name, None)
                if value is None:
                    continue
                assert (
                    "{" not in value
                ), f"{key!r}.{field_name} uses f-string syntax: {value!r}"


class TestCoverage:
    def test_africa_first_locales_present(self):
        """The Africa-first wedge requires these locales to ship in v1.
        Adding a new locale to the strategic list means updating BOTH
        the spec and this test (deliberate friction)."""
        required = {"en-NG", "yo-NG", "sw-KE", "ha-NG", "ig-NG"}
        assert required.issubset(LOCALE_DEFAULTS.keys()), (
            f"Africa-first locales missing: {required - LOCALE_DEFAULTS.keys()}"
        )

    def test_default_english_present(self):
        assert "en-US" in LOCALE_DEFAULTS

    def test_french_present_for_european_market(self):
        assert "fr-FR" in LOCALE_DEFAULTS


class TestMaintainerRoster:
    def test_every_locale_has_a_maintainer(self):
        """Every shipped locale must have a recorded maintainer + last
        review date. Without it, we can't tell a year from now whether
        the entry is stale."""
        maintainer_locales = {entry.locale_code for entry in LOCALE_MAINTAINERS}
        for key in LOCALE_DEFAULTS:
            assert key in maintainer_locales, (
                f"locale {key!r} ships without a maintainer entry"
            )

    def test_maintainer_dates_are_iso8601(self):
        for entry in LOCALE_MAINTAINERS:
            try:
                date.fromisoformat(entry.last_reviewed_iso)
            except ValueError as exc:
                pytest.fail(
                    f"locale {entry.locale_code!r} maintainer date "
                    f"{entry.last_reviewed_iso!r} is not ISO-8601: {exc}"
                )

    def test_maintainer_handles_non_empty(self):
        for entry in LOCALE_MAINTAINERS:
            assert entry.maintainer.strip(), (
                f"locale {entry.locale_code!r} has empty maintainer handle"
            )


class TestPlaceholderRendering:
    """The locale templates feed through ``persona._safe_render_template``
    which uses ``string.Template``. Make sure the placeholders used in
    the dict are exactly those the renderer knows about, otherwise a
    template will leave ``$persona_name`` literal in the prompt."""

    _ALLOWED_PLACEHOLDERS = frozenset({"persona_name", "company_name", "role_title"})

    def test_only_known_placeholders_used(self):
        from string import Template

        for key, entry in LOCALE_DEFAULTS.items():
            for field_name in ("greeting", "signoff"):
                value = getattr(entry, field_name)
                if value is None:
                    continue
                # Identify placeholders via Template's pattern. Any
                # unknown ones leak through as literal $name in the
                # prompt — that's a rendering bug.
                template = Template(value)
                # ``identifiers()`` was added in Python 3.11; fall
                # back to a regex if it's missing.
                if hasattr(template, "get_identifiers"):
                    identifiers = set(template.get_identifiers())
                else:
                    import re
                    identifiers = set(
                        re.findall(r"\$([a-zA-Z_][a-zA-Z0-9_]*)", value)
                    )
                unknown = identifiers - self._ALLOWED_PLACEHOLDERS
                assert not unknown, (
                    f"{key!r}.{field_name} uses unknown placeholders: {unknown}"
                )


class TestLookupHelpers:
    def test_get_locale_defaults_returns_known(self):
        entry = get_locale_defaults("en-US")
        assert entry is not None
        assert entry.locale_code == "en-US"

    def test_get_locale_defaults_returns_none_for_unknown(self):
        assert get_locale_defaults("xx-ZZ") is None

    def test_get_locale_defaults_does_not_fall_back_to_english(self):
        """Critical: a missing locale must return None, NOT silently
        substitute English. Otherwise an author who set ``locale_code``
        to a niche value would see English copy without realising it."""
        result = get_locale_defaults("zz-ZZ")
        assert result is None

    def test_list_supported_locales_returns_sorted_tuple(self):
        locales = list_supported_locales()
        assert isinstance(locales, tuple)
        assert list(locales) == sorted(locales)

    def test_list_supported_locales_matches_dict(self):
        assert set(list_supported_locales()) == set(LOCALE_DEFAULTS.keys())

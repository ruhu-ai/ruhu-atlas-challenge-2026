"""Phase 2b — curated locale defaults for the Persona Studio.

Each entry holds the **default** greeting/signoff/honorifics/calendar
language a tenant gets when they pick a locale and don't override the
cosmetic templates. The strings are an authored knowledge artifact —
NOT machine-translated — because wrong tone in a customer-facing
greeting is worse than no localisation.

Authoring contract:

* Each locale has a ``maintainer`` and ``last_reviewed_iso`` recorded
  in the metadata helper at the bottom of this file. Entries older
  than 12 months should be flagged for re-review (a separate sweep,
  not in this PR).
* New locales arrive only after a native speaker has reviewed the
  greeting + signoff and (if relevant) cultural-calendar phrases.
* Templates use the same ``$persona_name`` / ``$company_name`` /
  ``$role_title`` placeholders as ``CosmeticPersona.greeting_template``;
  rendering goes through ``persona._safe_render_template`` so prompt
  injection is bounded to known keys.

This module is intentionally data-only. Lookup logic (e.g. choosing
the right greeting at composition time) lives in
``persona.compose_persona_block``.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class LocaleDefaults:
    """Per-locale defaults used by the persona renderer.

    All fields are optional (the dataclass default is the empty/None
    case) so a partially-authored locale doesn't bleed in unwanted
    behaviour. ``ramadan_greeting`` is opt-in per [README decision 2-5];
    we never emit it unless the agent's ``cultural_calendar_enabled``
    is true AND the locale entry has the field populated."""

    locale_code: str
    """BCP-47 lang-region — must match the dictionary key."""

    greeting: str | None = None
    """Default opening line. ``$persona_name`` / ``$company_name`` /
    ``$role_title`` placeholders are honoured."""

    signoff: str | None = None
    """Default closing line. Same placeholder rules."""

    honorifics: tuple[str, ...] = ()
    """Common honorifics for the locale, e.g. ``("oga", "madam")`` for
    en-NG. Surfaced to the LLM as a soft hint only — the model uses
    them when grammatically natural; we never force them."""

    currency_symbol: str | None = None
    """Currency glyph for the locale (₦, KES, $, €). Used by tools
    that surface money to the user; the persona renderer leaves
    formatting to the LLM."""

    date_format: str = "DD/MM/YYYY"
    """Strict-format hint for tool outputs. The LLM follows this when
    composing prompts that surface dates."""

    ramadan_greeting: str | None = None
    """Opt-in. ``cultural_calendar_enabled`` AND non-None → injected
    into the greeting block during the Ramadan/Eid window. The window
    itself is computed elsewhere (a date helper not in this PR)."""

    christmas_greeting: str | None = None
    """Same opt-in semantics as ``ramadan_greeting``."""


# ── Curated entries ─────────────────────────────────────────────────────────
#
# NOTE on placeholder syntax: these strings are rendered via
# ``persona._safe_render_template`` which uses ``string.Template``.
# Don't use ``str.format`` syntax or you'll get unrendered
# ``{persona_name}`` text in the system prompt.

LOCALE_DEFAULTS: dict[str, LocaleDefaults] = {
    "en-US": LocaleDefaults(
        locale_code="en-US",
        greeting="Hi, $persona_name here. How can I help you today?",
        signoff="Thanks for chatting with $company_name.",
        honorifics=(),
        currency_symbol="$",
        date_format="MM/DD/YYYY",
    ),
    "en-GB": LocaleDefaults(
        locale_code="en-GB",
        greeting="Hello, this is $persona_name. How may I help you today?",
        signoff="Thank you for getting in touch with $company_name.",
        honorifics=(),
        currency_symbol="£",
        date_format="DD/MM/YYYY",
    ),
    "en-NG": LocaleDefaults(
        locale_code="en-NG",
        greeting="Welcome, $persona_name here. How can I help today?",
        signoff="Thanks for chatting — $persona_name out.",
        honorifics=("sir", "ma", "oga", "madam", "aunty"),
        currency_symbol="₦",
        date_format="DD/MM/YYYY",
        ramadan_greeting="Barka da Sallah.",
        christmas_greeting="Compliments of the season.",
    ),
    "yo-NG": LocaleDefaults(
        locale_code="yo-NG",
        greeting="Bawo ni, mo ni $persona_name. Ki ni mo le ran o lowo lori?",
        signoff="O sé.",
        honorifics=("egbon", "anti", "uncle", "mama"),
        currency_symbol="₦",
        date_format="DD/MM/YYYY",
        ramadan_greeting="Barka da Sallah.",
    ),
    "ha-NG": LocaleDefaults(
        locale_code="ha-NG",
        greeting="Sannu, ni ne $persona_name. Yaya zan iya taimaka maka?",
        signoff="Na gode da hira.",
        honorifics=("malam", "hajiya"),
        currency_symbol="₦",
        date_format="DD/MM/YYYY",
        ramadan_greeting="Barka da Sallah.",
    ),
    "ig-NG": LocaleDefaults(
        locale_code="ig-NG",
        greeting="Ndewo, abu m $persona_name. Kedu ka m ga-esi nyere gi aka?",
        signoff="Daalu maka ikwurita.",
        honorifics=("nna", "nne"),
        currency_symbol="₦",
        date_format="DD/MM/YYYY",
        christmas_greeting="Ezi ekeresimesi.",
    ),
    "sw-KE": LocaleDefaults(
        locale_code="sw-KE",
        greeting="Karibu, mimi ni $persona_name. Naweza kukusaidia vipi?",
        signoff="Asante sana.",
        honorifics=("bwana", "bibi"),
        currency_symbol="KES",
        date_format="DD/MM/YYYY",
        ramadan_greeting="Eid Mubarak.",
        christmas_greeting="Heri ya Krismasi.",
    ),
    "fr-FR": LocaleDefaults(
        locale_code="fr-FR",
        greeting="Bonjour, je suis $persona_name. Comment puis-je vous aider ?",
        signoff="Merci de votre échange avec $company_name.",
        honorifics=("monsieur", "madame"),
        currency_symbol="€",
        date_format="DD/MM/YYYY",
    ),
}


# ── Authoring metadata ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _MaintainerEntry:
    locale_code: str
    maintainer: str
    last_reviewed_iso: str
    notes: str = ""


# Maintainer roster. Each entry should be re-reviewed by a native
# speaker at least annually; older entries get flagged in a separate
# sweep (not in this PR). Putting the data here rather than in inline
# comments makes it greppable by a future "who reviewed yo-NG and
# when?" query.
LOCALE_MAINTAINERS: tuple[_MaintainerEntry, ...] = (
    _MaintainerEntry(
        locale_code="en-US",
        maintainer="@core",
        last_reviewed_iso="2026-05-09",
        notes="Default global English; minimal locale-specific tone.",
    ),
    _MaintainerEntry(
        locale_code="en-GB",
        maintainer="@core",
        last_reviewed_iso="2026-05-09",
    ),
    _MaintainerEntry(
        locale_code="en-NG",
        maintainer="@native-en-ng",
        last_reviewed_iso="2026-05-09",
        notes=(
            "Lagos register; honorifics are Nigerian English specific. "
            "Pidgin is a separate locale (out of scope for v1; promote "
            "from Phase 4 backlog when a native maintainer is available)."
        ),
    ),
    _MaintainerEntry(
        locale_code="yo-NG",
        maintainer="@native-yo-ng",
        last_reviewed_iso="2026-05-09",
        notes="Standard Yoruba; tonal marks deliberately omitted in greetings to keep ASCII-safe in transports without UTF-8 guarantees.",
    ),
    _MaintainerEntry(
        locale_code="ha-NG",
        maintainer="@native-ha-ng",
        last_reviewed_iso="2026-05-09",
    ),
    _MaintainerEntry(
        locale_code="ig-NG",
        maintainer="@native-ig-ng",
        last_reviewed_iso="2026-05-09",
    ),
    _MaintainerEntry(
        locale_code="sw-KE",
        maintainer="@native-sw-ke",
        last_reviewed_iso="2026-05-09",
        notes="Standard Coastal Swahili; sw-TZ would warrant separate entry.",
    ),
    _MaintainerEntry(
        locale_code="fr-FR",
        maintainer="@native-fr-fr",
        last_reviewed_iso="2026-05-09",
        notes="Mainland France; fr-CI / fr-SN are separate locales for African Francophone markets.",
    ),
)


# ── Public helpers ──────────────────────────────────────────────────────────


def get_locale_defaults(locale_code: str) -> LocaleDefaults | None:
    """Look up locale defaults for a BCP-47 lang-region tag.

    Returns ``None`` when the locale isn't curated. Callers must
    handle the ``None`` case (typically by falling back to the agent's
    cosmetic greeting/signoff templates). Don't auto-fall-back to
    ``en-US`` here — that would silently override an author's tenant
    locale choice with English copy.
    """
    return LOCALE_DEFAULTS.get(locale_code)


def list_supported_locales() -> tuple[str, ...]:
    """Return the curated locale codes in stable, sorted order.

    Used by the ``GET /persona/locales`` endpoint to populate the
    locale picker UI."""
    return tuple(sorted(LOCALE_DEFAULTS.keys()))


__all__ = [
    "LOCALE_DEFAULTS",
    "LOCALE_MAINTAINERS",
    "LocaleDefaults",
    "get_locale_defaults",
    "list_supported_locales",
]

"""Persona Studio schemas + system-prompt composition.

Two layers:

* :class:`CosmeticPersona` — live-edit identity surface (name, pronouns, avatar,
  greeting, sign-off, role title). Lives on ``AgentSettings.persona`` and is
  stored in the ``agents.settings_json`` JSON column. Patches apply immediately.

* :class:`BehavioralPersona` — versioned behaviour surface (formality, emoji
  policy, restricted-topic guidance). Lives on ``AgentDocument.metadata.persona``
  and goes through the existing draft → publish-review → publish flow.

Both are composed into the system prompt via :func:`compose_persona_block`,
which is wired through :meth:`AgentSettings.composed_system_prompt`.

Phase 1 design notes:

* Restricted topics are **best-effort guidance** — they are injected into the
  prompt and labelled as such. Phase 2 will wire them into ``rules.py`` for
  deterministic post-render enforcement.
* Voice persona is covered by the same composition seam: the LiveKit voice
  path renders through the same ``response_generation`` pipeline, so a
  ``persona_name`` on the cosmetic block reaches voice greetings automatically.
* Inputs go directly into a system prompt, so every free-text field uses
  hard length limits, character allowlists, and a ``string.Template`` based
  formatter that rejects unknown placeholders. ``str.format`` is **not** used
  because it permits attribute access (``{x.__class__}``) on attacker-controlled
  values.
"""
from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from string import Template
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Validation primitives ────────────────────────────────────────────────────

# Markup / template-injection vectors that are dangerous **everywhere** in a
# persona surface: angle brackets (HTML), backticks (Markdown / shell-style
# injection bait), curly braces (str.format placeholders that could read
# attributes — even though we don't use str.format ourselves, defence in depth).
# BCP-47 patterns. The full BCP-47 grammar is much richer than this
# (script subtags, variant subtags, extensions) but we only ever need
# language-level (``en``, ``yo``) and language-region (``en-US``,
# ``yo-NG``) shapes — keeping the pattern small means clear errors
# instead of accepting malformed input that breaks the catalog later.
_BCP47_LANG_PATTERN = re.compile(r"^[a-z]{2,3}(-[A-Z][a-zA-Z0-9]{1,8})?$")
_BCP47_LOCALE_PATTERN = re.compile(r"^[a-z]{2,3}-[A-Z]{2}$")

_DANGEROUS_CHARS_BASE: tuple[str, ...] = ("<", ">", "`", "{", "}")

# ``$`` is the ``string.Template`` substitution marker that we use for
# greeting/signoff rendering. Templates legitimately need ``$persona_name``,
# but any string that gets *substituted into* a template (persona_name,
# role_title, restricted_topics) must not contain ``$`` itself, otherwise the
# combined rendered string would have unresolved markers.
_DANGEROUS_CHARS_STRICT: tuple[str, ...] = _DANGEROUS_CHARS_BASE + ("$",)


def _has_control_char(value: str) -> bool:
    """Reject anything in Unicode category ``C*`` except plain ``\\n``.

    ``Cc`` (control), ``Cf`` (format), ``Cs`` (surrogate), ``Co`` (private use),
    ``Cn`` (unassigned) — none belong in a persona name or template. Newline is
    handled selectively via ``allow_single_newline``.
    """
    for ch in value:
        if ch == "\n":
            continue
        if unicodedata.category(ch).startswith("C"):
            return True
    return False


def _reject_dangerous(
    value: str,
    *,
    allow_dollar: bool,
    allow_single_newline: bool,
) -> None:
    """Common validation used by every free-text persona field.

    ``allow_dollar`` is ``True`` for greeting/signoff templates (where ``$key``
    is the substitution syntax), ``False`` for substituted values like
    ``persona_name`` and topic strings.
    """
    forbidden = _DANGEROUS_CHARS_BASE if allow_dollar else _DANGEROUS_CHARS_STRICT
    if any(ch in value for ch in forbidden):
        raise ValueError(
            "contains disallowed characters (one of "
            + ", ".join(forbidden)
            + ")"
        )
    if _has_control_char(value):
        raise ValueError("contains control characters")
    if "\n\n" in value:
        raise ValueError("contains paragraph break")
    if not allow_single_newline and "\n" in value:
        raise ValueError("contains newline")
    if "\t" in value or "\r" in value:
        raise ValueError("contains tab or carriage return")


def _validate_persona_name(value: str) -> str:
    """Validate a persona name.

    Length 1–60. Disallows control characters, dangerous markup characters,
    and leading/trailing whitespace (which would render badly). Accepts the
    full range of Unicode letters and combining marks so names like
    ``Mayọ̀wá`` (Yoruba), ``Müller`` (German), ``Patrícia`` (Portuguese), and
    ``陈`` (Chinese) work — the Africa-first persona work depends on this.
    """
    if not value or len(value) > 60:
        raise ValueError("persona_name must be 1-60 characters")
    if value != value.strip():
        raise ValueError("persona_name must not have leading/trailing whitespace")
    _reject_dangerous(value, allow_dollar=False, allow_single_newline=False)
    return value


# ── Schemas ──────────────────────────────────────────────────────────────────


class CosmeticPersona(BaseModel):
    """Live-edit persona surface. PATCH applies immediately, no publish required."""

    model_config = ConfigDict(extra="forbid")

    persona_name: str | None = Field(default=None, max_length=60)
    pronouns: Literal["she/her", "he/him", "they/them", "custom"] | None = None
    pronouns_custom: str | None = Field(default=None, max_length=30)
    avatar_url: str | None = Field(default=None, max_length=512)
    role_title: str | None = Field(default=None, max_length=100)
    greeting_template: str | None = Field(default=None, max_length=500)
    signoff_template: str | None = Field(default=None, max_length=300)
    # Phase 2b — per-language persona name overrides. Branding for
    # markets where the canonical persona_name doesn't translate well
    # ("Maya" in English → "Mayọ̀wá" in Yoruba). Keys are BCP-47
    # language tags, values follow persona_name validation. Cosmetic
    # because it's branding, not behaviour — live-edit, no publish.
    persona_name_overrides: dict[str, str] = Field(
        default_factory=dict, max_length=12,
    )

    @field_validator("persona_name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_persona_name(value)

    @field_validator("persona_name_overrides")
    @classmethod
    def _validate_name_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        for lang, name in value.items():
            if not _BCP47_LANG_PATTERN.match(lang):
                raise ValueError(
                    f"persona_name_overrides keys must be BCP-47 tags, got {lang!r}"
                )
            # Same validation as persona_name itself — names go straight
            # into the system prompt, so the prompt-injection guards
            # apply to overrides too.
            _validate_persona_name(name)
        return value

    @field_validator("avatar_url")
    @classmethod
    def _validate_avatar(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.startswith("https://"):
            raise ValueError("avatar_url must be HTTPS")
        return value

    @field_validator("role_title", "pronouns_custom")
    @classmethod
    def _validate_short_text(cls, value: str | None) -> str | None:
        # These flow as substitution values into greeting/signoff templates;
        # ``$`` is therefore disallowed (would create unresolved markers).
        if value is None:
            return value
        _reject_dangerous(value, allow_dollar=False, allow_single_newline=False)
        return value

    @field_validator("greeting_template", "signoff_template")
    @classmethod
    def _validate_template(cls, value: str | None) -> str | None:
        # Templates may contain ``$persona_name`` etc — ``$`` is allowed.
        # Single newlines are allowed (e.g., a two-line greeting); paragraph
        # breaks are not.
        if value is None:
            return value
        _reject_dangerous(value, allow_dollar=True, allow_single_newline=True)
        return value


class TopicEnforcementPolicy(StrEnum):
    """Three-mode policy for restricted-topic handling.

    See ``docs/persona/phase-2.md`` Track 2c for the design and
    ``docs/persona/README.md`` decision [2-1] for the rollout strategy.

    Modes:

    * ``off`` — no detection, no logging. Topic guidance still appears in the
      composed system prompt (so the LLM is *asked* to avoid topics) but no
      post-render guard runs.
    * ``log_only`` — canary mode. Detection runs and decisions are emitted as
      ``topic_violation_logged`` audit events, but the response still goes out
      unmodified. Tenants use this for 7 days post-merge to vet their topic
      list before flipping to enforcement.
    * ``block_and_retry`` — full enforcement. On detected violation: log,
      retry render with a stronger constraint, and if the retry also violates,
      fall back to a deterministic deflection.
    """

    off = "off"
    log_only = "log_only"
    block_and_retry = "block_and_retry"


class AutoSwitchMode(StrEnum):
    """Three-mode policy for the language router.

    Pulled forward from Phase 3b per [README decision 3-1]. Modelled
    identically to ``TopicEnforcementPolicy`` so authors use the same
    canary-then-enforce pattern across both surfaces.

    * ``off`` — language detection NEVER runs. Agent stays in
      ``primary_language`` regardless of what the user speaks.
    * ``log_only`` — detection runs, decisions are audited as
      ``language.switched_shadowed``, but the agent stays in the
      primary language. Use to vet detection accuracy on real traffic
      before flipping to ``on``.
    * ``on`` — full behaviour. Stability gates pass → agent matches the
      user's detected language (subject to ``allowed_languages`` and
      the unsupported-language policy).
    """

    off = "off"
    log_only = "log_only"
    on = "on"


class LanguageSwitchPolicy(StrEnum):
    """How the agent reacts to a language switch *once* the stability
    gates pass. See spec for the empirical justification."""

    mirror_user = "mirror_user"
    """Always match the user's most recent detected language. Default
    for African markets where code-switching is normal conversational
    behaviour."""

    lock_to_primary = "lock_to_primary"
    """Ignore detected language; stay in primary. Use for branded
    voice agents where the persona's voice must be consistent."""

    gradual_revert = "gradual_revert"
    """Switch to detected language, then revert to primary after 3
    turns of single-language input. Compromise position."""


class UnsupportedLanguagePolicy(StrEnum):
    """How the agent responds when the user speaks a language NOT in
    ``allowed_languages``."""

    stay_in_primary = "stay_in_primary"
    """Silent fallback — agent responds in primary language; user is
    not told their language isn't supported. Risk: feels unresponsive."""

    explain_and_offer = "explain_and_offer"
    """Default. Agent politely explains it only supports the configured
    languages and asks the user to continue in one of them. Vapi's
    documented production pattern."""

    escalate_to_human = "escalate_to_human"
    """Triggers a handoff. Only meaningful when the agent has HITL
    routing wired. Otherwise behaves identically to
    ``stay_in_primary`` (with an audit event)."""


class BehavioralPersona(BaseModel):
    """Versioned persona behaviour. Lives on ``AgentDocument.metadata.persona``.

    Changes require draft → publish-review → publish.
    """

    model_config = ConfigDict(extra="forbid")

    formality: Literal["formal", "neutral", "casual"] = "neutral"
    emoji_policy: Literal["never", "sparingly", "encouraged"] = "sparingly"
    # Phase 2c: topic enforcement (see ``topic_enforcement.py``).
    restricted_topics: list[str] = Field(default_factory=list, max_length=10)
    topic_enforcement: TopicEnforcementPolicy = TopicEnforcementPolicy.log_only

    # Phase 2a-base — voice. ``voice_provider`` is the provider key
    # registered with ``voice/factory.py`` (currently only
    # ``"vertex_gemini"``); ``voice_id`` is the provider-specific
    # identifier from the catalog. ``voice_speed`` is clamped here so the
    # value is always safe to pass to ``VoiceProvider.synthesize``.
    # ``voice_monthly_budget_cents`` is reserved for 2a-paid (None means
    # unlimited; warning at 80% lands with the paid providers, where cost
    # is meaningful).
    voice_provider: str = Field(default="vertex_gemini", min_length=1, max_length=64)
    voice_id: str = Field(default="en-US-Chirp3-HD-Kore", min_length=1, max_length=128)
    voice_speed: float = Field(default=1.0, ge=0.7, le=1.3)
    voice_monthly_budget_cents: int | None = Field(default=None, ge=0)

    # ── Phase 2b — Multi-language + locale + voice-per-language ──────────
    #
    # Defaults are chosen so existing agents (no Phase 2b config) produce
    # byte-identical behaviour to today: English-only, no auto-switch,
    # no overrides, no cultural-calendar greetings. The Phase 1 golden
    # test (test_compose_persona_block_byte_identical_when_unset) is
    # extended in this PR with an absent-state assertion to lock that
    # backwards-compat contract in.
    #
    # ``primary_language`` accepts the special value ``"auto"`` for
    # tenants on Gemini Live native audio or Soniox who want
    # truly-automatic detection; this is documented as advanced and
    # validated explicitly in the field validator.

    primary_language: str = Field(default="en", min_length=2, max_length=16)
    allowed_languages: list[str] = Field(
        default_factory=lambda: ["en"], max_length=12,
    )
    auto_switch_language: AutoSwitchMode = AutoSwitchMode.off
    # Stability gates — applied in order. Defaults from Phase 2 spec
    # research (Salesforce Agentforce uses 0.82 in APAC; we ship 0.80
    # as a slightly less conservative starting point with all three
    # tunable per-tenant).
    language_switch_confidence_threshold: float = Field(default=0.80, ge=0.5, le=0.99)
    language_switch_min_chars: int = Field(default=10, ge=0, le=200)
    language_switch_debounce_turns: int = Field(default=1, ge=0, le=5)
    language_switch_policy: LanguageSwitchPolicy = LanguageSwitchPolicy.mirror_user
    unsupported_language_policy: UnsupportedLanguagePolicy = (
        UnsupportedLanguagePolicy.explain_and_offer
    )
    # Per-language voice mapping. Keys are BCP-47 language tags. Values
    # are voice IDs from the Vertex Gemini provider's catalog (or, for
    # tenants who've used 2a-cloning, clone IDs). Validation happens
    # at publish-review time, not at PATCH time, because the catalog
    # can change as clones are added.
    voice_id_overrides: dict[str, str] = Field(default_factory=dict, max_length=12)

    locale_code: str = Field(default="en-US", min_length=2, max_length=16)
    cultural_calendar_enabled: bool = False

    @field_validator("primary_language")
    @classmethod
    def _validate_primary_language(cls, value: str) -> str:
        # Special "auto" token enables Gemini-Live-native-audio /
        # Soniox truly-automatic detection without forcing a language
        # allowlist. Everywhere else, require a BCP-47-shaped tag.
        if value == "auto":
            return value
        if not _BCP47_LANG_PATTERN.match(value):
            raise ValueError(
                f"primary_language must be 'auto' or a BCP-47 tag, got {value!r}"
            )
        return value

    @field_validator("allowed_languages")
    @classmethod
    def _validate_allowed_languages(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("allowed_languages must contain at least one language")
        for lang in value:
            if not _BCP47_LANG_PATTERN.match(lang):
                raise ValueError(
                    f"allowed_languages must be BCP-47 tags, got {lang!r}"
                )
        return value

    @field_validator("locale_code")
    @classmethod
    def _validate_locale(cls, value: str) -> str:
        if not _BCP47_LOCALE_PATTERN.match(value):
            raise ValueError(
                f"locale_code must be a BCP-47 lang-region tag (e.g. en-US), got {value!r}"
            )
        return value

    @field_validator("voice_id_overrides")
    @classmethod
    def _validate_voice_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        for lang, voice_id in value.items():
            if not _BCP47_LANG_PATTERN.match(lang):
                raise ValueError(
                    f"voice_id_overrides keys must be BCP-47 tags, got {lang!r}"
                )
            if not voice_id or len(voice_id) > 128:
                raise ValueError(
                    f"voice_id for language {lang!r} must be 1-128 chars"
                )
        return value

    @field_validator("restricted_topics")
    @classmethod
    def _validate_topics(cls, value: list[str]) -> list[str]:
        for topic in value:
            if not topic or len(topic) > 200:
                raise ValueError("restricted_topic must be 1-200 chars")
            _reject_dangerous(topic, allow_dollar=False, allow_single_newline=False)
        return value


# ── Composition ──────────────────────────────────────────────────────────────


_FORMALITY_COPY: dict[str, str] = {
    "formal": "Use a formal, precise register.",
    "neutral": "Use a neutral, professional register.",
    "casual": "Use a friendly, casual register.",
}

_EMOJI_COPY: dict[str, str] = {
    "never": "Do not use emoji.",
    "sparingly": "Use emoji sparingly.",
    "encouraged": "Use emoji to add warmth where natural.",
}


def _safe_render_template(
    template_str: str,
    *,
    persona_name: str | None,
    company_name: str | None,
    role_title: str | None,
) -> str:
    """Render a persona template using ``string.Template``.

    ``string.Template`` is preferred over ``str.format`` because it permits only
    ``$identifier`` substitutions (no attribute access, no positional args).
    Unknown identifiers raise :class:`KeyError`, which we swallow and return the
    raw template — never substituting attacker-controlled data.
    """
    mapping = {
        "persona_name": persona_name or "",
        "company_name": company_name or "",
        "role_title": role_title or "",
    }
    try:
        return Template(template_str).substitute(mapping)
    except (KeyError, ValueError):
        return template_str


def compose_persona_block(
    cosmetic: CosmeticPersona | None,
    behavioral: BehavioralPersona | None,
    company_name: str | None,
    *,
    effective_language: str | None = None,
) -> str:
    """Return the persona prefix for the system prompt.

    Returns ``""`` (empty string) when neither layer is configured —
    guarantees existing agents (no persona) produce **byte-identical** prompts
    to today. The golden test in ``tests/test_persona.py`` locks this in.

    ``effective_language`` is the BCP-47 language tag the renderer
    decided to respond in for this turn (after the language router's
    stability gates pass). When provided AND
    ``cosmetic.persona_name_overrides[language]`` is set, the agent
    introduces itself with the localised name; otherwise it falls back
    to the canonical ``persona_name``. Phase 2b backwards-compat: when
    no overrides are configured, the rendered output is byte-identical
    to today.
    """
    if cosmetic is None and behavioral is None:
        return ""

    cosmetic = cosmetic or CosmeticPersona()
    behavioral = behavioral or BehavioralPersona()

    # Resolve persona name with optional per-language override. Strip
    # the region from BCP-47 ("yo-NG" → "yo") for override lookup
    # because branding is usually language-level, not region-level.
    resolved_persona_name = cosmetic.persona_name
    if effective_language and cosmetic.persona_name_overrides:
        lang_key = effective_language.split("-", 1)[0]
        override = cosmetic.persona_name_overrides.get(
            effective_language,
        ) or cosmetic.persona_name_overrides.get(lang_key)
        if override:
            resolved_persona_name = override

    lines: list[str] = []

    if resolved_persona_name:
        identity = f"You are {resolved_persona_name}"
        if cosmetic.role_title:
            identity += f", a {cosmetic.role_title}"
        if company_name:
            identity += f" at {company_name}"
        lines.append(identity + ".")

    pronouns = (
        cosmetic.pronouns_custom
        if cosmetic.pronouns == "custom"
        else cosmetic.pronouns
    )
    if pronouns:
        lines.append(f"Your pronouns are {pronouns}.")

    lines.append(_FORMALITY_COPY[behavioral.formality])
    lines.append(_EMOJI_COPY[behavioral.emoji_policy])

    # Phase 2b — language directives. We emit these only when the
    # tenant has actually configured multi-language behaviour, so
    # English-only agents (the default) produce byte-identical prompts
    # to Phase 2a.
    if (
        behavioral.allowed_languages != ["en"]
        or behavioral.primary_language != "en"
    ):
        if behavioral.primary_language == "auto":
            lines.append(
                "Match the user's language automatically. "
                "Use whatever language they speak in."
            )
        else:
            allowed = ", ".join(behavioral.allowed_languages)
            lines.append(
                f"You speak: {allowed}. Match the user's language within that set."
            )
            if (
                len(behavioral.allowed_languages) > 1
                and behavioral.unsupported_language_policy
                == UnsupportedLanguagePolicy.explain_and_offer
            ):
                lines.append(
                    f"If the user speaks a language other than {allowed}, "
                    "politely explain you only support these and ask them "
                    "to continue in one of them."
                )

    if behavioral.restricted_topics:
        topics = "; ".join(behavioral.restricted_topics)
        # Phase 2c: prompt copy is mode-aware. The decorator
        # ``TopicEnforcingResponseGenerator`` does the actual post-render
        # enforcement; the prompt's job is to bias the LLM toward compliance.
        if behavioral.topic_enforcement == TopicEnforcementPolicy.block_and_retry:
            lines.append(
                f"Important constraint — do not discuss the following topics: {topics}."
            )
        else:
            # off + log_only — same softer phrasing. The "off" case still
            # benefits from a soft prompt nudge; "log_only" doesn't promise
            # enforcement so the wording stays soft.
            lines.append(f"Topic guidance — avoid discussing: {topics}.")

    if cosmetic.greeting_template:
        rendered = _safe_render_template(
            cosmetic.greeting_template,
            persona_name=resolved_persona_name,
            company_name=company_name,
            role_title=cosmetic.role_title,
        )
        lines.append(f'When greeting the user, use phrasing like: "{rendered}"')

    if cosmetic.signoff_template:
        rendered = _safe_render_template(
            cosmetic.signoff_template,
            persona_name=resolved_persona_name,
            company_name=company_name,
            role_title=cosmetic.role_title,
        )
        lines.append(f'When closing the conversation, use phrasing like: "{rendered}"')

    return "\n".join(lines)


__all__ = [
    "AutoSwitchMode",
    "BehavioralPersona",
    "CosmeticPersona",
    "LanguageSwitchPolicy",
    "TopicEnforcementPolicy",
    "UnsupportedLanguagePolicy",
    "compose_persona_block",
]

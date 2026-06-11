from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_NON_IDENTIFIER_CHARS = re.compile(r"[^a-zA-Z0-9_]+")


def callable_name_for_ref(ref: str) -> str:
    """Return the Action Code-safe callable alias for a tool ref/category."""
    alias = _NON_IDENTIFIER_CHARS.sub("_", str(ref or "").strip()).strip("_").lower()
    if not alias:
        return "tool"
    if alias[0].isdigit():
        return f"tool_{alias}"
    return alias


@dataclass(frozen=True)
class ActionConfigCallableBindings:
    callable_names: list[str]
    direct_call_aliases: dict[str, str]
    integration_aliases: dict[str, str]


def build_action_config_callable_bindings(
    *,
    callable_api_refs: Iterable[str] = (),
    callable_system_refs: Iterable[str] = (),
    callable_integrations: Iterable[str] = (),
) -> ActionConfigCallableBindings:
    direct_call_aliases: dict[str, str] = {}
    integration_aliases: dict[str, str] = {}

    for ref in callable_api_refs:
        alias = callable_name_for_ref(ref)
        direct_call_aliases.setdefault(alias, ref)
    for ref in callable_system_refs:
        alias = callable_name_for_ref(ref)
        direct_call_aliases.setdefault(alias, ref)
    for category in callable_integrations:
        alias = callable_name_for_ref(category)
        integration_aliases.setdefault(alias, category)

    callable_names = list(dict.fromkeys([*direct_call_aliases.keys(), *integration_aliases.keys()]))
    return ActionConfigCallableBindings(
        callable_names=callable_names,
        direct_call_aliases=direct_call_aliases,
        integration_aliases=integration_aliases,
    )

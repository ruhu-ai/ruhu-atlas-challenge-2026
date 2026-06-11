from __future__ import annotations

from typing import Iterable

from .specs import ToolSpec


class ToolRegistry:
    """Pure spec catalog. No execution logic belongs here."""

    def __init__(self, specs: Iterable[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.ref in self._specs:
            raise ValueError(f"duplicate tool ref: {spec.ref}")
        self._specs[spec.ref] = spec.model_copy(deep=True)

    def get(self, ref: str) -> ToolSpec | None:
        spec = self._specs.get(ref)
        return spec.model_copy(deep=True) if spec else None

    def require(self, ref: str) -> ToolSpec:
        spec = self.get(ref)
        if spec is None:
            raise KeyError(ref)
        return spec

    def has(self, ref: str) -> bool:
        return ref in self._specs

    def list(self) -> list[ToolSpec]:
        return [spec.model_copy(deep=True) for _, spec in sorted(self._specs.items())]

    def by_kind(self, kind: str) -> list[ToolSpec]:
        return [spec.model_copy(deep=True) for spec in self._specs.values() if spec.kind == kind]


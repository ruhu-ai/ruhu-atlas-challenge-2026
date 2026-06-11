from __future__ import annotations

from typing import Any

from jinja2 import BaseLoader, StrictUndefined
from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment, SecurityError


class TemplateRenderError(ValueError):
    pass


class _RestrictedSandboxEnvironment(SandboxedEnvironment):
    _BLOCKED_ATTRIBUTES = {
        "__class__",
        "__mro__",
        "__subclasses__",
        "__bases__",
        "__init__",
        "__globals__",
        "__code__",
        "__dict__",
        "__self__",
        "__func__",
        "__module__",
        "__builtins__",
        "__import__",
        "mro",
        "func_globals",
        "func_code",
        "f_back",
        "f_globals",
        "f_locals",
    }
    _BLOCKED_CALLABLES = {
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "type",
    }

    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        if attr.startswith("_") or attr in self._BLOCKED_ATTRIBUTES:
            return False
        return super().is_safe_attribute(obj, attr, value)

    def is_safe_callable(self, obj: Any) -> bool:
        name = getattr(obj, "__name__", "")
        if name in self._BLOCKED_CALLABLES or obj is type:
            return False
        return super().is_safe_callable(obj)


class SecureTemplateRenderer:
    _MAX_TEMPLATE_LENGTH = 10_000
    _MAX_OUTPUT_LENGTH = 100_000

    def __init__(self) -> None:
        self._env = _RestrictedSandboxEnvironment(
            loader=BaseLoader(),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._env.globals.update(
            {
                "len": len,
                "str": str,
                "int": int,
                "float": float,
                "bool": bool,
                "list": list,
                "dict": dict,
                "min": min,
                "max": max,
                "sum": sum,
                "sorted": sorted,
                "enumerate": enumerate,
                "range": range,
            }
        )

    def render(self, template: str, context: dict[str, Any]) -> str:
        if len(template) > self._MAX_TEMPLATE_LENGTH:
            raise TemplateRenderError("template exceeds maximum length")
        try:
            rendered = self._env.from_string(template).render(context)
        except (TemplateError, SecurityError) as exc:
            raise TemplateRenderError(str(exc)) from exc
        if len(rendered) > self._MAX_OUTPUT_LENGTH:
            raise TemplateRenderError("rendered template exceeds maximum length")
        return rendered

    def render_value(self, value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str):
            return self.render(value, context) if ("{{" in value or "{%" in value) else value
        if isinstance(value, list):
            return [self.render_value(item, context) for item in value]
        if isinstance(value, dict):
            return {
                str(key): self.render_value(item, context)
                for key, item in value.items()
            }
        return value

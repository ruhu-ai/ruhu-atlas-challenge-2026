from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .types import ToolChannel, ToolFailureKind, ToolKind, ToolOutputValidationMode
from ruhu.validation.schema import JsonSchemaValidator, ValidationError as SchemaValidationError

ToolAuthMode = Literal["none", "secret_ref", "service_account", "oauth2", "session"]
ToolConfirmationMode = Literal["never", "always", "destructive_only"]

TOOL_REF_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")


class ToolAnnotations(BaseModel):
    read_only: bool = False
    destructive: bool = False
    side_effect_free: bool = False
    idempotent: bool = False
    open_world: bool = False

    @model_validator(mode="after")
    def validate_flags(self) -> "ToolAnnotations":
        if self.side_effect_free and self.destructive:
            raise ValueError("side_effect_free tools cannot be destructive")
        if self.side_effect_free and not self.read_only:
            self.read_only = True
        return self


class ToolInputExample(BaseModel):
    name: str
    description: str
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_example(self) -> "ToolInputExample":
        if len(self.name.strip()) < 3:
            raise ValueError("tool input example name must be at least 3 characters")
        if len(self.description.strip()) < 10:
            raise ValueError("tool input example description must be at least 10 characters")
        return self


class ToolFailureMode(BaseModel):
    kind: ToolFailureKind
    description: str
    retryable: bool = False

    @model_validator(mode="after")
    def validate_failure_mode(self) -> "ToolFailureMode":
        if len(self.description.strip()) < 10:
            raise ValueError("tool failure mode description must be at least 10 characters")
        return self


class ToolSpec(BaseModel):
    ref: str
    kind: ToolKind
    display_name: str
    description: str
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    )
    output_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
    )
    annotations: ToolAnnotations = Field(default_factory=ToolAnnotations)
    timeout_ms: int = Field(default=3_000, ge=1, le=600_000)
    confirmation: ToolConfirmationMode = "never"
    confirmation_prompt: str | None = None
    auth_mode: ToolAuthMode = "none"
    executor_key: str | None = None
    executor_config: dict[str, Any] = Field(default_factory=dict)
    allowed_channels: list[ToolChannel] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    purpose: str | None = None
    when_to_use: list[str] = Field(default_factory=list)
    when_not_to_use: list[str] = Field(default_factory=list)
    input_examples: list[ToolInputExample] = Field(default_factory=list)
    failure_modes: list[ToolFailureMode] = Field(default_factory=list)
    output_validation_mode: ToolOutputValidationMode = "warn"
    # Maps fact names → extraction expressions over the tool result output.
    # An expression starting with "$." is a dotted JSONPath-lite traversal
    # (e.g., "$.data.user.name"); anything else is treated as a top-level
    # key into result.output. The kernel applies this mapping after a
    # successful tool call to produce explicit fact writes.
    output_mapping: dict[str, str] = Field(default_factory=dict)
    # Refs of other Library callables this code body is permitted to invoke.
    # Only meaningful for kind='code'. Each entry must resolve to another
    # ToolSpec.ref the runtime knows about. The CodeExecutor's sandbox
    # bridge translates author-friendly aliases (see ``callable_aliases``)
    # back to refs before invoking the runtime.
    callable_refs: list[str] = Field(default_factory=list)
    # Maps the sandbox-visible function name (the alias) to the underlying
    # tool ref. Authors see ``get_user_profile()`` in the editor; the
    # executor bridge translates that to ``code.fetch_user_profile`` (or
    # whatever the real ref is) before ``runtime.invoke()``. Required when
    # two callable_refs would collide on their default short name (e.g.,
    # ``crm.get_user`` and ``banking.get_user`` both want ``get_user``).
    callable_aliases: dict[str, str] = Field(default_factory=dict)

    # Internal: cached validators (created on first use)
    _input_validator: JsonSchemaValidator | None = None
    _output_validator: JsonSchemaValidator | None = None

    @model_validator(mode="after")
    def validate_spec(self) -> "ToolSpec":
        if not TOOL_REF_PATTERN.match(self.ref):
            raise ValueError("tool ref must be lowercase and may include digits, dashes, underscores, and dots")
        if len(self.description.strip()) < 20:
            raise ValueError("tool description must be at least 20 characters")
        if self.input_schema.get("type") != "object":
            raise ValueError("input_schema.type must be 'object'")
        if self.output_schema.get("type") != "object":
            raise ValueError("output_schema.type must be 'object'")
        if self.executor_key is None:
            self.executor_key = self.ref
        if self.purpose is not None:
            self.purpose = self.purpose.strip()
            if len(self.purpose) < 20:
                raise ValueError("tool purpose must be at least 20 characters")
        self.when_to_use = self._normalize_guidance_list(self.when_to_use, field_name="when_to_use")
        self.when_not_to_use = self._normalize_guidance_list(
            self.when_not_to_use,
            field_name="when_not_to_use",
        )
        for example in self.input_examples:
            try:
                self.validate_input(example.args)
            except SchemaValidationError as exc:
                raise ValueError(
                    f"tool input example {example.name!r} does not match input_schema: {exc.message}"
                ) from exc
        self._validate_callable_bindings()
        return self

    def _validate_callable_bindings(self) -> None:
        """``callable_refs``/``callable_aliases`` are only meaningful on code
        callables; reject them elsewhere so the bridge is never silently
        misconfigured for an HTTP/integration tool. Also enforce that every
        declared alias resolves to a declared ref and that aliases are
        unique sandbox identifiers."""
        if (self.callable_refs or self.callable_aliases) and self.kind != "code":
            raise ValueError(
                f"callable_refs/callable_aliases are only valid for kind='code', got {self.kind!r}"
            )
        if not self.callable_refs:
            if self.callable_aliases:
                raise ValueError("callable_aliases declared but callable_refs is empty")
            return
        ref_set = set(self.callable_refs)
        if len(ref_set) != len(self.callable_refs):
            raise ValueError("callable_refs must not contain duplicates")
        for alias, ref in self.callable_aliases.items():
            if not alias.isidentifier() or alias.startswith("_"):
                raise ValueError(
                    f"callable alias {alias!r} must be a valid Python identifier and "
                    "must not start with an underscore (RestrictedPython sandbox rule)"
                )
            if ref not in ref_set:
                raise ValueError(
                    f"callable alias {alias!r} maps to {ref!r}, which is not in callable_refs"
                )

    @staticmethod
    def _normalize_guidance_list(items: list[str], *, field_name: str) -> list[str]:
        normalized: list[str] = []
        for item in items:
            value = str(item).strip()
            if len(value) < 10:
                raise ValueError(f"{field_name} entries must be at least 10 characters")
            normalized.append(value)
        return normalized

    def validate_input(self, args: dict[str, Any]) -> dict[str, Any]:
        """Validate tool input args against input_schema.

        Args:
            args: Input arguments dict.

        Returns:
            The same args (unchanged) if valid.

        Raises:
            SchemaValidationError: If validation fails.
        """
        if not self._input_validator:
            self._input_validator = JsonSchemaValidator(self.input_schema)

        try:
            return self._input_validator.validate(args)
        except SchemaValidationError as e:
            # Re-raise with tool context
            raise SchemaValidationError(
                f"Tool {self.ref} input validation failed: {e.message}",
                path=e.path,
                value=e.value,
                schema_path=e.schema_path,
            ) from e

    def validate_output(self, output: dict[str, Any]) -> dict[str, Any]:
        """Validate tool output against output_schema.

        Unlike input validation, this is lenient. We log warnings but don't fail.
        This allows tools to return slightly different shapes without crashing.

        Args:
            output: Output dict to validate.

        Returns:
            The same output (unchanged).
        """
        if not self._output_validator:
            self._output_validator = JsonSchemaValidator(self.output_schema)

        try:
            return self._output_validator.validate(output)
        except SchemaValidationError as e:
            if self.output_validation_mode == "strict":
                raise SchemaValidationError(
                    f"Tool {self.ref} output validation failed: {e.message}",
                    path=e.path,
                    value=e.value,
                    schema_path=e.schema_path,
                ) from e
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "Tool %s output validation warning: %s at %s. "
                "Proceeding with unvalidated output (may cause downstream errors).",
                self.ref,
                e.message,
                e.path,
            )
            return output

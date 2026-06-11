"""Expression DSL parser for rule predicates.

Hand-written recursive-descent parser that compiles DSL expressions like
`facts.is_vip == true and turn.text contains "refund"` into the canonical
RulePredicate AST used by the rules engine.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .rules import (
    AllPredicate,
    AnyPredicate,
    MatchPredicate,
    NotPredicate,
    RulePredicate,
)


@dataclass(frozen=True)
class ExpressionParseError(Exception):
    """Raised on DSL parse errors with position info."""
    message: str
    pos: int

    def __str__(self) -> str:
        return f"{self.message} at position {self.pos}"


class _Tokenizer:
    """Splits expression into tokens."""

    PATTERNS = [
        ("LPAREN", r"\("),
        ("RPAREN", r"\)"),
        ("LBRACKET", r"\["),
        ("RBRACKET", r"\]"),
        ("COMMA", r","),
        ("EQ", r"=="),
        ("NE", r"!="),
        ("LE", r"<="),
        ("GE", r">="),
        ("LT", r"<"),
        ("GT", r">"),
        ("EXCLAIM", r"!"),
        ("AMPAMP", r"&&"),
        ("PIPEPIPE", r"\|\|"),
        ("DOT", r"\."),
        ("KEYWORD_NOT_IN", r"\bnot\s+in\b"),
        ("KEYWORD_BETWEEN", r"\bbetween\b"),
        ("KEYWORD_CONTAINS", r"\bcontains\b"),
        ("KEYWORD_MATCHES", r"\bmatches\b"),
        ("KEYWORD_EXISTS", r"\bexists\b"),
        ("NOT_LITERAL", r"\b(?:not|NOT)\b"),
        ("AND_LITERAL", r"\b(?:and|AND)\b"),
        ("OR_LITERAL", r"\b(?:or|OR)\b"),
        ("KEYWORD_IN", r"\bin\b"),
        ("STRING", r'"(?:[^"\\]|\\.)*"'),
        ("NUMBER", r"-?\d+(?:\.\d+)?"),
        ("IDENT", r"[a-zA-Z_][a-zA-Z0-9_]*"),
        ("WHITESPACE", r"\s+"),
    ]

    COMPILED = [(name, re.compile(pattern)) for name, pattern in PATTERNS]

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.tokens: list[tuple[str, str, int]] = []
        self._tokenize()

    def _tokenize(self) -> None:
        while self.pos < len(self.text):
            match_found = False
            for name, pattern in self.COMPILED:
                match = pattern.match(self.text, self.pos)
                if match:
                    value = match.group(0)
                    if name != "WHITESPACE":
                        self.tokens.append((name, value, self.pos))
                    self.pos = match.end()
                    match_found = True
                    break
            if not match_found:
                raise ExpressionParseError(f"Unexpected character: {self.text[self.pos]!r}", self.pos)

    def peek(self, offset: int = 0) -> tuple[str, str, int] | None:
        idx = offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return None

    def consume(self, expected_type: str | None = None) -> tuple[str, str, int]:
        if not self.tokens:
            raise ExpressionParseError("Unexpected end of input", self.pos)
        token_type, value, pos = self.tokens.pop(0)
        if expected_type and token_type != expected_type:
            raise ExpressionParseError(f"Expected {expected_type}, got {token_type}", pos)
        return token_type, value, pos


class _Parser:
    """Recursive-descent parser for expressions."""

    def __init__(self, tokenizer: _Tokenizer):
        self.tokenizer = tokenizer

    def parse(self) -> RulePredicate:
        result = self._or_expr()
        if self.tokenizer.peek():
            raise ExpressionParseError("Unexpected token at end", 0)
        return result

    def _or_expr(self) -> RulePredicate:
        left = self._and_expr()
        while self.tokenizer.peek() and self.tokenizer.peek()[0] in ("OR_LITERAL", "PIPEPIPE"):
            self.tokenizer.consume()
            right = self._and_expr()
            left = AnyPredicate(predicates=[left, right])
        return left

    def _and_expr(self) -> RulePredicate:
        left = self._not_expr()
        while self.tokenizer.peek() and self.tokenizer.peek()[0] in ("AND_LITERAL", "AMPAMP"):
            self.tokenizer.consume()
            right = self._not_expr()
            left = AllPredicate(predicates=[left, right])
        return left

    def _not_expr(self) -> RulePredicate:
        if self.tokenizer.peek() and self.tokenizer.peek()[0] in ("NOT_LITERAL", "EXCLAIM"):
            self.tokenizer.consume()
            expr = self._not_expr()
            return NotPredicate(predicate=expr)
        return self._cmp_expr()

    def _cmp_expr(self) -> RulePredicate:
        if self.tokenizer.peek() and self.tokenizer.peek()[0] == "LPAREN":
            self.tokenizer.consume()
            expr = self._or_expr()
            self.tokenizer.consume("RPAREN")
            return expr

        path = self._parse_path()

        # Check for operators
        token = self.tokenizer.peek()
        if not token:
            raise ExpressionParseError("Expected operator after path", 0)

        if token[0] == "KEYWORD_EXISTS":
            self.tokenizer.consume()
            return MatchPredicate(path=path, operator="exists")

        if token[0] == "KEYWORD_BETWEEN":
            self.tokenizer.consume()
            self.tokenizer.consume("LBRACKET")
            lower = self._parse_literal()
            self.tokenizer.consume("COMMA")
            upper = self._parse_literal()
            self.tokenizer.consume("RBRACKET")
            return MatchPredicate(path=path, operator="between", lower=lower, upper=upper)

        if token[0] == "KEYWORD_NOT_IN":
            self.tokenizer.consume()
            values = self._parse_literal_list()
            return MatchPredicate(path=path, operator="not_in", values=values)

        if token[0] == "KEYWORD_IN":
            self.tokenizer.consume()
            values = self._parse_literal_list()
            return MatchPredicate(path=path, operator="in", values=values)

        op_map = {
            "EQ": "eq",
            "NE": "neq",
            "LT": "lt",
            "LE": "lte",
            "GT": "gt",
            "GE": "gte",
            "KEYWORD_CONTAINS": "contains",
            "KEYWORD_MATCHES": "regex",
        }
        if token[0] in op_map:
            op_type = self.tokenizer.consume()[0]
            value = self._parse_literal()
            return MatchPredicate(path=path, operator=op_map[op_type], value=value)

        raise ExpressionParseError(f"Unexpected token: {token[0]}", token[2])

    def _parse_path(self) -> str:
        parts = []
        token = self.tokenizer.consume("IDENT")
        parts.append(token[1])
        while self.tokenizer.peek() and self.tokenizer.peek()[0] == "DOT":
            self.tokenizer.consume()
            token = self.tokenizer.consume("IDENT")
            parts.append(token[1])
        return ".".join(parts)

    def _parse_literal(self) -> str | int | float | bool | None:
        token = self.tokenizer.peek()
        if not token:
            raise ExpressionParseError("Expected literal", 0)

        if token[0] == "STRING":
            self.tokenizer.consume()
            # Remove quotes and handle escape sequences
            val = token[1][1:-1].replace('\\"', '"').replace("\\\\", "\\")
            return val
        if token[0] == "NUMBER":
            self.tokenizer.consume()
            val = token[1]
            return int(val) if "." not in val else float(val)
        if token[1] in ("true", "True"):
            self.tokenizer.consume()
            return True
        if token[1] in ("false", "False"):
            self.tokenizer.consume()
            return False
        if token[1] == "null":
            self.tokenizer.consume()
            return None
        raise ExpressionParseError(f"Expected literal, got {token[0]}", token[2])

    def _parse_literal_list(self) -> list:
        self.tokenizer.consume("LBRACKET")
        values = [self._parse_literal()]
        while self.tokenizer.peek() and self.tokenizer.peek()[0] == "COMMA":
            self.tokenizer.consume()
            values.append(self._parse_literal())
        self.tokenizer.consume("RBRACKET")
        return values


def compile_expression(expr: str) -> RulePredicate:
    """Parse DSL expression and return the canonical predicate AST."""
    tokenizer = _Tokenizer(expr)
    parser = _Parser(tokenizer)
    return parser.parse()


def render_expression(predicate: RulePredicate) -> str:
    """Round-trip AST back to DSL form (for canvas display)."""
    def _format_value(v: object) -> str:
        if isinstance(v, str):
            return json.dumps(v)
        return str(v)

    if isinstance(predicate, MatchPredicate):
        op_map = {
            "eq": "==",
            "neq": "!=",
            "lt": "<",
            "lte": "<=",
            "gt": ">",
            "gte": ">=",
            "contains": "contains",
            "regex": "matches",
            "exists": "exists",
            "in": "in",
            "not_in": "not in",
            "between": "between",
        }
        op = op_map.get(predicate.operator, predicate.operator)
        if predicate.operator == "exists":
            return f"{predicate.path} exists"
        if predicate.operator == "between":
            return f"{predicate.path} between [{predicate.lower}, {predicate.upper}]"
        if predicate.operator in ("in", "not_in"):
            vals = ", ".join(_format_value(v) for v in (predicate.values or []))
            return f"{predicate.path} {op} [{vals}]"
        val_str = _format_value(predicate.value)
        return f"{predicate.path} {op} {val_str}"
    if isinstance(predicate, AllPredicate):
        parts = [render_expression(p) for p in predicate.predicates]
        return " and ".join(f"({p})" if " or " in p else p for p in parts)
    if isinstance(predicate, AnyPredicate):
        parts = [render_expression(p) for p in predicate.predicates]
        return " or ".join(f"({p})" if " and " in p else p for p in parts)
    if isinstance(predicate, NotPredicate):
        inner = render_expression(predicate.predicate)
        return f"not ({inner})"
    return str(predicate)

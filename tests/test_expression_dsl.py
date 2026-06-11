"""Tests for expression DSL parser and rule integration."""
import pytest

from ruhu.rules import (
    AllPredicate,
    AnyPredicate,
    MatchPredicate,
    NotPredicate,
    RuleDefinition,
    BlockEffect,
)
from ruhu.rules_dsl import compile_expression, render_expression, ExpressionParseError


def test_compile_simple_equality():
    """Test compiling a simple equality expression."""
    expr = "facts.is_vip == true"
    predicate = compile_expression(expr)
    assert isinstance(predicate, MatchPredicate)
    assert predicate.path == "facts.is_vip"
    assert predicate.operator == "eq"
    assert predicate.value is True


def test_compile_not_equal():
    """Test compiling a not-equal expression."""
    expr = "turn.text != \"goodbye\""
    predicate = compile_expression(expr)
    assert isinstance(predicate, MatchPredicate)
    assert predicate.operator == "neq"
    assert predicate.value == "goodbye"


def test_compile_contains():
    """Test compiling a contains expression."""
    expr = "turn.text contains \"refund\""
    predicate = compile_expression(expr)
    assert isinstance(predicate, MatchPredicate)
    assert predicate.operator == "contains"
    assert predicate.value == "refund"


def test_compile_regex():
    """Test compiling a regex expression."""
    expr = "turn.text matches \"[0-9]{3}-[0-9]{3}-[0-9]{4}\""
    predicate = compile_expression(expr)
    assert isinstance(predicate, MatchPredicate)
    assert predicate.operator == "regex"


def test_compile_in():
    """Test compiling an in expression."""
    expr = "turn.event_type in [\"user_message\", \"system_event\"]"
    predicate = compile_expression(expr)
    assert isinstance(predicate, MatchPredicate)
    assert predicate.operator == "in"
    assert predicate.values == ["user_message", "system_event"]


def test_compile_not_in():
    """Test compiling a not_in expression."""
    expr = "conversation.channel not in [\"phone\", \"whatsapp\"]"
    predicate = compile_expression(expr)
    assert isinstance(predicate, MatchPredicate)
    assert predicate.operator == "not_in"
    assert predicate.values == ["phone", "whatsapp"]


def test_compile_exists():
    """Test compiling an exists expression."""
    expr = "facts.custom_flag exists"
    predicate = compile_expression(expr)
    assert isinstance(predicate, MatchPredicate)
    assert predicate.operator == "exists"


def test_compile_between():
    """Test compiling a between expression."""
    expr = "turn.text_length between [100, 500]"
    predicate = compile_expression(expr)
    assert isinstance(predicate, MatchPredicate)
    assert predicate.operator == "between"
    assert predicate.lower == 100
    assert predicate.upper == 500


def test_compile_comparison_operators():
    """Test various comparison operators."""
    tests = [
        ("metadata.age > 18", "gt", 18),
        ("metadata.age >= 18", "gte", 18),
        ("metadata.age < 65", "lt", 65),
        ("metadata.age <= 65", "lte", 65),
    ]
    for expr, expected_op, expected_val in tests:
        predicate = compile_expression(expr)
        assert predicate.operator == expected_op
        assert predicate.value == expected_val


def test_compile_and():
    """Test compiling an AND expression."""
    expr = "facts.is_vip == true and turn.text contains \"refund\""
    predicate = compile_expression(expr)
    assert isinstance(predicate, AllPredicate)
    assert len(predicate.predicates) == 2


def test_compile_or():
    """Test compiling an OR expression."""
    expr = "turn.event_type == \"user_message\" or turn.event_type == \"system_event\""
    predicate = compile_expression(expr)
    assert isinstance(predicate, AnyPredicate)
    assert len(predicate.predicates) == 2


def test_compile_not():
    """Test compiling a NOT expression."""
    expr = "not (facts.is_vip == true)"
    predicate = compile_expression(expr)
    assert isinstance(predicate, NotPredicate)
    assert isinstance(predicate.predicate, MatchPredicate)


def test_compile_operator_precedence():
    """Test that operator precedence is correct (not > and > or)."""
    expr = "facts.a == true or facts.b == true and facts.c == true"
    predicate = compile_expression(expr)
    assert isinstance(predicate, AnyPredicate)
    assert isinstance(predicate.predicates[1], AllPredicate)


def test_compile_parentheses():
    """Test that parentheses override precedence."""
    expr = "(facts.a == true or facts.b == true) and facts.c == true"
    predicate = compile_expression(expr)
    assert isinstance(predicate, AllPredicate)
    assert isinstance(predicate.predicates[0], AnyPredicate)


def test_compile_case_insensitive_keywords():
    """Test that keywords are case-insensitive."""
    exprs = [
        "facts.flag == true AND facts.flag2 == false",
        "facts.flag == true and facts.flag2 == false",
    ]
    for expr in exprs:
        predicate = compile_expression(expr)
        assert isinstance(predicate, AllPredicate)


def test_compile_operator_aliases():
    """Test operator aliases like && for and, || for or."""
    expr1 = "facts.a == true && facts.b == true"
    expr2 = "facts.a == true || facts.b == true"
    pred1 = compile_expression(expr1)
    pred2 = compile_expression(expr2)
    assert isinstance(pred1, AllPredicate)
    assert isinstance(pred2, AnyPredicate)


def test_compile_numeric_values():
    """Test parsing numeric values (int and float)."""
    expr_int = "metadata.age > 18"
    expr_float = "metadata.score > 3.14"
    pred_int = compile_expression(expr_int)
    pred_float = compile_expression(expr_float)
    assert pred_int.value == 18
    assert pred_float.value == 3.14


def test_compile_negative_numbers():
    """Test parsing negative numbers."""
    expr = "metadata.balance < -100"
    predicate = compile_expression(expr)
    assert predicate.value == -100


def test_compile_string_with_escapes():
    """Test parsing strings with escape sequences."""
    expr = 'turn.text contains "say \\"hello\\""'
    predicate = compile_expression(expr)
    assert predicate.value == 'say "hello"'


def test_render_simple_equality():
    """Test rendering a simple equality predicate."""
    predicate = MatchPredicate(path="facts.is_vip", operator="eq", value=True)
    expr = render_expression(predicate)
    assert "facts.is_vip" in expr
    assert "==" in expr
    assert "True" in expr


def test_render_complex_expression():
    """Test rendering a complex nested predicate."""
    predicate = AllPredicate(
        predicates=[
            MatchPredicate(path="facts.is_vip", operator="eq", value=True),
            AnyPredicate(
                predicates=[
                    MatchPredicate(path="turn.text", operator="contains", value="refund"),
                    MatchPredicate(path="turn.text", operator="contains", value="return"),
                ]
            ),
        ]
    )
    expr = render_expression(predicate)
    assert "facts.is_vip" in expr
    assert "and" in expr
    assert "or" in expr


def test_round_trip():
    """Test that parsing and rendering yields equivalent expressions."""
    original = "facts.is_vip == true and (turn.text contains \"refund\" or turn.text contains \"return\")"
    predicate = compile_expression(original)
    rendered = render_expression(predicate)
    predicate_again = compile_expression(rendered)
    assert render_expression(predicate) == render_expression(predicate_again)


def test_rule_definition_with_expression():
    """Test creating a RuleDefinition with expression field."""
    rule = RuleDefinition(
        rule_id="test.rule",
        name="Test Rule",
        summary="Test rule with expression",
        stage="turn_ingress",
        expression="facts.is_vip == true",
        effect=BlockEffect(code="test", message="Test"),
    )
    assert rule.expression == "facts.is_vip == true"
    assert isinstance(rule.predicate, MatchPredicate)
    assert rule.predicate.path == "facts.is_vip"
    assert rule.predicate.operator == "eq"
    assert rule.predicate.value is True


def test_rule_definition_with_predicate_only():
    """Test that RuleDefinition still works with predicate only."""
    rule = RuleDefinition(
        rule_id="test.rule",
        name="Test Rule",
        summary="Test rule with predicate",
        stage="turn_ingress",
        predicate=MatchPredicate(path="facts.flag", operator="eq", value=True),
        effect=BlockEffect(code="test", message="Test"),
    )
    assert rule.predicate is not None
    assert rule.expression is None


def test_rule_definition_expression_takes_precedence():
    """Test that expression is compiled when both predicate and expression are set."""
    # When expression is set and predicate is None, expression should compile
    rule = RuleDefinition(
        rule_id="test.rule",
        name="Test Rule",
        summary="Test rule",
        stage="turn_ingress",
        expression="turn.text contains \"test\"",
        effect=BlockEffect(code="test", message="Test"),
    )
    assert rule.expression == "turn.text contains \"test\""
    assert isinstance(rule.predicate, MatchPredicate)
    assert rule.predicate.operator == "contains"


def test_rule_definition_requires_predicate_or_expression():
    """Test that RuleDefinition requires either predicate or expression."""
    with pytest.raises(ValueError, match="One of predicate or expression must be provided"):
        RuleDefinition(
            rule_id="test.rule",
            name="Test Rule",
            summary="Test rule",
            stage="turn_ingress",
            effect=BlockEffect(code="test", message="Test"),
        )


def test_parse_error_position():
    """Test that parse errors include position information."""
    with pytest.raises(ExpressionParseError) as exc_info:
        compile_expression("facts.flag == !!invalid")
    assert "position" in str(exc_info.value).lower()


def test_parse_error_unexpected_character():
    """Test parsing error for unexpected character."""
    with pytest.raises(ExpressionParseError):
        compile_expression("facts.flag == true @@@")


def test_parse_error_unexpected_token():
    """Test parsing error for unexpected token."""
    with pytest.raises(ExpressionParseError):
        compile_expression("facts.flag == true and and")


def test_parse_error_unexpected_end():
    """Test parsing error for unexpected end of input."""
    with pytest.raises(ExpressionParseError):
        compile_expression("facts.flag ==")

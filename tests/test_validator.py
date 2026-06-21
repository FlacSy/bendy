from bendy.type_mapper import resolve_field
from bendy.types import MISSING, AggregateInfo, FieldInfo, ManifestResult, ValueObjectInfo
from bendy.validator import validate


def _result(aggregates=None, value_objects=None):
    return ManifestResult(
        aggregates=aggregates or [],
        enums=[],
        value_objects=value_objects or [],
        errors=[],
    )


def _field(name, type_hint=str, default=MISSING):
    return resolve_field(name, type_hint, default)


def _agg(name, fields=None, use_cases=None):
    return AggregateInfo(
        name=name,
        fields=fields or [_field("name")],
        use_cases=use_cases or ["create", "get"],
    )


def _errors(result):
    return validate(result)


def test_valid():
    assert _errors(_result([_agg("Order")])) == []


def test_duplicate_aggregate():
    errors = _errors(_result([_agg("Order"), _agg("Order")]))
    assert any("duplicate" in e for e in errors)


def test_lowercase_name():
    errors = _errors(_result([_agg("order")]))
    assert any("uppercase" in e for e in errors)


def test_empty_aggregate():
    errors = _errors(_result([AggregateInfo("Order", [], ["create"])]))
    assert any("at least one field" in e for e in errors)


def test_reserved_id_field():
    errors = _errors(_result([AggregateInfo("Order", [_field("id")], ["create"])]))
    assert any("reserved" in e for e in errors)


def test_python_keyword_field():
    f = FieldInfo("class", "str", "str", "String", "str", False, False, None)
    errors = _errors(_result([AggregateInfo("Order", [f], ["create"])]))
    assert any("keyword" in e for e in errors)


def test_auto_now_optional():
    # construct the forbidden combination directly — resolve_field would reject it
    f = FieldInfo(
        "ts",
        "Optional[datetime]",
        "datetime",
        "DateTime",
        "Optional[datetime]",
        True,
        False,
        None,
        auto_now=True,
    )
    errors = _errors(_result([AggregateInfo("Order", [f], ["create"])]))
    assert any("auto_now" in e for e in errors)


def test_max_length_on_non_str():
    f = FieldInfo("count", "int", "int", "Integer", "int", False, False, None, max_length=10)
    errors = _errors(_result([AggregateInfo("Order", [f], ["create"])]))
    assert any("max_length" in e for e in errors)


def test_unique_nullable_warning():
    f = FieldInfo(
        "code",
        "Optional[str]",
        "str",
        "String",
        "Optional[str]",
        True,
        False,
        None,
        unique=True,
    )
    errors = _errors(_result([AggregateInfo("Order", [f], ["create"])]))
    assert any("duplicate NULLs" in e for e in errors)


def test_empty_value_object():
    vo = ValueObjectInfo(name="Empty", fields=[])
    errors = _errors(_result(value_objects=[vo]))
    assert any("at least one field" in e for e in errors)


def test_multiple_errors_reported():
    # lowercase name + reserved field — both should surface
    errors = _errors(_result([AggregateInfo("order", [_field("id")], ["create"])]))
    assert len(errors) >= 2

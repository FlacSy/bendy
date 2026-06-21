import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

import pytest

from bendy.field import Field
from bendy.type_mapper import resolve_field
from bendy.types import AUTO_NOW, MISSING
from bendy.value_object import ValueObject


class Status(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Address(ValueObject):
    street: str
    city: str


# --- scalar types ---


@pytest.mark.parametrize(
    ("type_hint", "python_type", "sa_type"),
    [
        (str, "str", "String"),
        (int, "int", "Integer"),
        (float, "float", "Float"),
        (bool, "bool", "Boolean"),
        (Decimal, "Decimal", "Numeric"),
        (datetime.datetime, "datetime", "DateTime"),
        (datetime.date, "date", "Date"),
        (UUID, "UUID", "String"),
    ],
)
def test_scalar(type_hint, python_type, sa_type):
    f = resolve_field("x", type_hint, MISSING)
    assert f.python_type == python_type
    assert f.sa_column_type == sa_type
    assert not f.nullable
    assert not f.has_default


def test_optional():
    f = resolve_field("x", str | None, MISSING)
    assert f.python_type == "Optional[str]"
    assert f.nullable
    assert f.sa_column_type == "String"


def test_default():
    f = resolve_field("status", str, "active")
    assert f.has_default
    assert f.default_repr == "'active'"


def test_unsupported_type():
    with pytest.raises(TypeError, match="unsupported type"):
        resolve_field("x", list, MISSING)


# --- auto_now ---


def test_auto_now_sentinel():
    f = resolve_field("created_at", datetime.datetime, AUTO_NOW)
    assert f.auto_now
    assert not f.has_default


def test_auto_now_via_field():
    f = resolve_field("created_at", datetime.datetime, Field(auto_now=True))
    assert f.auto_now


def test_auto_now_wrong_type():
    with pytest.raises(TypeError, match="auto_now"):
        resolve_field("name", str, AUTO_NOW)


def test_field_auto_now_and_default_conflict():
    with pytest.raises(ValueError, match="auto_now"):
        Field(default="x", auto_now=True)


# --- Field() metadata ---


def test_field_metadata():
    f = resolve_field("email", str, Field(unique=True, index=True, max_length=255))
    assert f.unique
    assert f.index
    assert f.max_length == 255
    assert not f.has_default


def test_field_with_default():
    f = resolve_field("status", str, Field(default="pending"))
    assert f.has_default
    assert f.default_repr == "'pending'"


# --- Enum ---


def test_enum():
    f = resolve_field("status", Status, Status.ACTIVE)
    assert f.is_enum
    assert f.enum_class_name == "Status"
    assert f.sa_column_type == "String"
    assert f.default_repr == "Status.ACTIVE"


def test_enum_optional():
    f = resolve_field("status", Status | None, MISSING)
    assert f.nullable
    assert f.python_type == "Optional[Status]"
    assert f.sa_mapped_type == "Optional[str]"


def test_enum_no_default():
    f = resolve_field("status", Status, MISSING)
    assert not f.has_default
    assert f.default_repr is None


# --- ValueObject ---


def test_value_object():
    f = resolve_field("address", Address, MISSING)
    assert f.is_value_object
    assert f.vo_class_name == "Address"
    assert f.sa_column_type == "JSON"
    assert f.python_type == "Address"


def test_value_object_optional():
    f = resolve_field("address", Address | None, MISSING)
    assert f.nullable
    assert f.python_type == "Optional[Address]"
    assert f.sa_mapped_type == "Optional[dict]"

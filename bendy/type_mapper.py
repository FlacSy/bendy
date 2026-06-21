import datetime
import inspect
from decimal import Decimal
from enum import Enum
from typing import Union, get_args, get_origin
from uuid import UUID

from .field import Field
from .types import AUTO_NOW, MISSING, FieldInfo
from .value_object import ValueObject

_SCALAR_MAP: dict[type, tuple[str, str, str]] = {
    str: ("str", "String", "str"),
    int: ("int", "Integer", "int"),
    float: ("float", "Float", "float"),
    bool: ("bool", "Boolean", "bool"),
    Decimal: ("Decimal", "Numeric", "Decimal"),
    datetime.datetime: ("datetime", "DateTime", "datetime"),
    datetime.date: ("date", "Date", "date"),
    UUID: ("UUID", "String", "UUID"),
}

_AUTO_NOW_TYPES = {"datetime", "date"}


def _enum_repr(value) -> str:
    if isinstance(value, Enum):
        return f"{type(value).__name__}.{value.name}"
    return repr(value)


def resolve_field(name: str, type_hint, raw_default) -> FieldInfo:
    meta: Field | None = None
    if isinstance(raw_default, Field):
        meta = raw_default
        raw_default = meta.default

    auto_now = (raw_default is AUTO_NOW) or (meta is not None and meta.auto_now)
    if auto_now:
        raw_default = MISSING

    nullable = False
    origin = get_origin(type_hint)
    if origin is Union:
        inner = [a for a in get_args(type_hint) if a is not type(None)]
        if len(inner) == 1 and type(None) in get_args(type_hint):
            type_hint = inner[0]
            nullable = True

    if inspect.isclass(type_hint) and issubclass(type_hint, Enum):
        base_py = type_hint.__name__
        has_default = raw_default is not MISSING
        return FieldInfo(
            name=name,
            python_type=f"Optional[{base_py}]" if nullable else base_py,
            base_python_type=base_py,
            sa_column_type="String",
            sa_mapped_type="Optional[str]" if nullable else "str",
            nullable=nullable,
            has_default=has_default,
            default_repr=_enum_repr(raw_default) if has_default else None,
            auto_now=auto_now,
            unique=meta.unique if meta else False,
            index=meta.index if meta else False,
            max_length=meta.max_length if meta else None,
            is_enum=True,
            enum_class_name=base_py,
        )

    if inspect.isclass(type_hint) and issubclass(type_hint, ValueObject):
        base_py = type_hint.__name__
        has_default = raw_default is not MISSING
        return FieldInfo(
            name=name,
            python_type=f"Optional[{base_py}]" if nullable else base_py,
            base_python_type=base_py,
            sa_column_type="JSON",
            sa_mapped_type="Optional[dict]" if nullable else "dict",
            nullable=nullable,
            has_default=has_default,
            default_repr=repr(raw_default) if has_default else None,
            unique=False,
            index=False,
            max_length=None,
            is_value_object=True,
            vo_class_name=base_py,
        )

    if type_hint not in _SCALAR_MAP:
        raise TypeError(f"field '{name}': unsupported type {type_hint!r}")

    base_py, sa_type, sa_mapped = _SCALAR_MAP[type_hint]

    if auto_now and base_py not in _AUTO_NOW_TYPES:
        raise TypeError(f"field '{name}': auto_now only applies to datetime/date")

    has_default = raw_default is not MISSING

    return FieldInfo(
        name=name,
        python_type=f"Optional[{base_py}]" if nullable else base_py,
        base_python_type=base_py,
        sa_column_type=sa_type,
        sa_mapped_type=f"Optional[{sa_mapped}]" if nullable else sa_mapped,
        nullable=nullable,
        has_default=has_default,
        default_repr=repr(raw_default) if has_default else None,
        auto_now=auto_now,
        unique=meta.unique if meta else False,
        index=meta.index if meta else False,
        max_length=meta.max_length if meta else None,
    )

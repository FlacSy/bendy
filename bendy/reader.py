import importlib.util
import inspect
import typing
from enum import Enum
from pathlib import Path

from .aggregate import Aggregate
from .type_mapper import resolve_field
from .types import (
    _DEFAULT_USE_CASES,
    MISSING,
    AggregateInfo,
    EnumInfo,
    FieldInfo,
    ManifestResult,
    ValueObjectInfo,
)
from .value_object import ValueObject

_VALID_USE_CASES = {"create", "get", "update", "delete", "list"}


def read_manifest(path: Path) -> ManifestResult:
    if not path.exists():
        return ManifestResult([], [], [], [f"file not found: {path}"])

    spec = importlib.util.spec_from_file_location("_bendy_manifest", path)
    module = importlib.util.module_from_spec(spec)

    try:
        spec.loader.exec_module(module)
    except SyntaxError as e:
        return ManifestResult([], [], [], [f"syntax error at line {e.lineno}: {e.msg}"])
    except Exception as e:
        return ManifestResult([], [], [], [f"failed to load manifest: {e}"])

    errors: list[str] = []
    enums: list[EnumInfo] = []
    value_objects: list[ValueObjectInfo] = []
    aggregates: list[AggregateInfo] = []

    for class_name, cls in inspect.getmembers(module, inspect.isclass):
        if issubclass(cls, Enum) and cls is not Enum:
            enums.append(EnumInfo(name=class_name, values=[(m.name, m.value) for m in cls]))
        elif issubclass(cls, ValueObject) and cls is not ValueObject:
            value_objects.append(
                ValueObjectInfo(name=class_name, fields=_fields(class_name, cls, errors))
            )
        elif issubclass(cls, Aggregate) and cls is not Aggregate:
            aggregates.append(
                AggregateInfo(
                    name=class_name,
                    fields=_fields(class_name, cls, errors),
                    use_cases=_use_cases(class_name, cls, errors),
                    unique_together=_composite_groups(class_name, cls, "unique_together", errors),
                    index_together=_composite_groups(class_name, cls, "index_together", errors),
                )
            )

    return ManifestResult(
        aggregates=aggregates, enums=enums, value_objects=value_objects, errors=errors
    )


def _fields(class_name: str, cls: type, errors: list[str]) -> list[FieldInfo]:
    try:
        hints = typing.get_type_hints(cls)
    except NameError as e:
        errors.append(f"[{class_name}] unknown type: {e}")
        return []

    fields = []
    for field_name, type_hint in hints.items():
        if field_name == "Meta":
            continue
        raw = cls.__dict__.get(field_name, MISSING)
        try:
            fields.append(resolve_field(field_name, type_hint, raw))
        except TypeError as e:
            errors.append(str(e))
    return fields


def _use_cases(class_name: str, cls: type, errors: list[str]) -> list[str]:
    meta = cls.__dict__.get("Meta")
    if meta is None:
        return list(_DEFAULT_USE_CASES)

    raw = getattr(meta, "use_cases", _DEFAULT_USE_CASES)
    invalid = set(raw) - _VALID_USE_CASES
    if invalid:
        errors.append(f"[{class_name}].Meta.use_cases: unknown operations: {sorted(invalid)}")
    return [uc for uc in raw if uc in _VALID_USE_CASES]


def _composite_groups(
    class_name: str, cls: type, attr_name: str, errors: list[str]
) -> list[tuple[str, ...]]:
    meta = cls.__dict__.get("Meta")
    if meta is None:
        return []

    raw = getattr(meta, attr_name, [])
    groups: list[tuple[str, ...]] = []
    for entry in raw:
        if not isinstance(entry, tuple | list) or not all(isinstance(x, str) for x in entry):
            errors.append(
                f"[{class_name}].Meta.{attr_name}: each entry must be a tuple of field "
                f"names, got {entry!r}"
            )
            continue
        groups.append(tuple(entry))
    return groups

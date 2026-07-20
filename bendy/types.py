from dataclasses import dataclass, field

MISSING = object()
AUTO_NOW = object()

_DEFAULT_USE_CASES = ["create", "get"]


@dataclass
class FieldInfo:
    name: str
    python_type: str
    base_python_type: str
    sa_column_type: str
    sa_mapped_type: str
    nullable: bool
    has_default: bool
    default_repr: str | None
    auto_now: bool = False
    unique: bool = False
    index: bool = False
    max_length: int | None = None
    is_enum: bool = False
    enum_class_name: str | None = None
    is_value_object: bool = False
    vo_class_name: str | None = None


@dataclass
class EnumInfo:
    name: str
    values: list[tuple[str, str]]


@dataclass
class ValueObjectInfo:
    name: str
    fields: list[FieldInfo]


@dataclass
class AggregateInfo:
    name: str
    fields: list[FieldInfo]
    use_cases: list[str] = field(default_factory=lambda: list(_DEFAULT_USE_CASES))
    unique_together: list[tuple[str, ...]] = field(default_factory=list)
    index_together: list[tuple[str, ...]] = field(default_factory=list)


@dataclass
class ManifestResult:
    aggregates: list[AggregateInfo]
    enums: list[EnumInfo]
    value_objects: list[ValueObjectInfo]
    errors: list[str]

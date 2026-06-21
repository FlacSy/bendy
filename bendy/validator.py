import keyword

from .types import FieldInfo, ManifestResult

_RESERVED_FIELDS = {"id"}


def validate(result: ManifestResult) -> list[str]:
    errors: list[str] = []
    seen_aggregates: set[str] = set()

    for agg in result.aggregates:
        p = f"[{agg.name}]"

        if agg.name in seen_aggregates:
            errors.append(f"{p} duplicate aggregate name")
        seen_aggregates.add(agg.name)

        if not agg.name[0].isupper():
            errors.append(f"{p} name must start with an uppercase letter")

        if not agg.fields:
            errors.append(f"{p} aggregate must have at least one field")
            continue

        errors.extend(_validate_fields(agg.name, agg.fields))

    for vo in result.value_objects:
        if not vo.fields:
            errors.append(f"[{vo.name}] (ValueObject) must have at least one field")
        else:
            errors.extend(_validate_fields(vo.name, vo.fields, reserved=set()))

    return errors


def _validate_fields(
    owner: str, fields: list[FieldInfo], reserved: set[str] = _RESERVED_FIELDS
) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()

    for f in fields:
        fp = f"[{owner}].{f.name}"

        if f.name in reserved:
            errors.append(f"{fp}: '{f.name}' is reserved and generated automatically")

        if not f.name.isidentifier():
            errors.append(f"{fp}: not a valid Python identifier")

        if keyword.iskeyword(f.name):
            errors.append(f"{fp}: '{f.name}' is a Python keyword")

        if f.name in seen:
            errors.append(f"{fp}: duplicate field name")
        seen.add(f.name)

        if f.auto_now and f.nullable:
            errors.append(f"{fp}: auto_now is incompatible with Optional")

        if f.max_length is not None and f.sa_column_type != "String":
            errors.append(f"{fp}: max_length is only applicable to str fields")

        if f.unique and f.nullable:
            errors.append(f"{fp}: unique + nullable may cause duplicate NULLs")

    return errors

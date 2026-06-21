import ast
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .types import AggregateInfo, EnumInfo, ManifestResult, ValueObjectInfo

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_AGGREGATE_TEMPLATES = {
    "domain_models.py.jinja": "domain/models.py",
    "domain_repository.py.jinja": "domain/repository.py",
    "app_dtos.py.jinja": "application/dtos.py",
    "app_use_cases.py.jinja": "application/use_cases.py",
    "router.py.jinja": "presentation/router.py",
    "infra_models.py.jinja": "infrastructure/models.py",
    "infra_repository.py.jinja": "infrastructure/repository.py",
    "infra_uow.py.jinja": "infrastructure/uow.py",
}

_DATETIME_IMPORTS = {"datetime", "date"}


def _make_env(vo_names: set[str]) -> Environment:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), keep_trailing_newline=True)

    def replace_vo_dto(python_type: str) -> str:
        for name in vo_names:
            python_type = python_type.replace(f"Optional[{name}]", f"Optional[{name}DTO]")
            python_type = python_type.replace(name, f"{name}DTO")
        return python_type

    env.filters["replace_vo_dto"] = replace_vo_dto
    return env


def _vo_context(vos: list[ValueObjectInfo]) -> dict:
    vo_has_optional = any(f.nullable for vo in vos for f in vo.fields)

    extra_raw: set[tuple[str, str]] = set()
    for vo in vos:
        for f in vo.fields:
            if f.base_python_type in ("datetime", "date"):
                extra_raw.add(("datetime", f.base_python_type))
            if f.base_python_type == "Decimal":
                extra_raw.add(("decimal", "Decimal"))

    enriched = [
        {
            "name": vo.name,
            "fields": vo.fields,
            "required_fields": [f for f in vo.fields if not f.has_default],
            "optional_fields": [f for f in vo.fields if f.has_default],
        }
        for vo in vos
    ]

    return {
        "value_objects": enriched,
        "vo_has_optional": vo_has_optional,
        "vo_extra_imports": [{"module": m, "name": n} for m, n in sorted(extra_raw)],
    }


def _generate_aggregate(
    agg: AggregateInfo,
    all_enums: list[EnumInfo],
    all_vos: list[ValueObjectInfo],
    output_dir: Path,
    env: Environment,
) -> list[str]:
    used_enum_names = {f.enum_class_name for f in agg.fields if f.is_enum}
    used_vo_names = {f.vo_class_name for f in agg.fields if f.is_value_object}

    relevant_enums = [e for e in all_enums if e.name in used_enum_names]
    relevant_vos = [v for v in all_vos if v.name in used_vo_names]

    extra_imports = {
        f.base_python_type
        for f in agg.fields
        if f.base_python_type in _DATETIME_IMPORTS | {"Decimal"}
    }
    sa_imports = {"String"} | {f.sa_column_type for f in agg.fields}
    if relevant_vos:
        sa_imports.add("JSON")

    auto_now_fields = [f for f in agg.fields if f.auto_now]

    ctx = {
        "domain_name": agg.name.lower(),
        "fields": agg.fields,
        "required_fields": [f for f in agg.fields if not f.has_default],
        "optional_fields": [f for f in agg.fields if f.has_default],
        "has_optional": any(f.nullable for f in agg.fields),
        "extra_imports": extra_imports,
        "sa_imports": sorted(sa_imports),
        "auto_now_fields": auto_now_fields,
        "has_auto_now": bool(auto_now_fields),
        "enums": relevant_enums,
        "use_cases": agg.use_cases,
        **_vo_context(relevant_vos),
    }

    errors = []
    for template_name, relative_path in _AGGREGATE_TEMPLATES.items():
        rendered = env.get_template(template_name).render(**ctx)
        out = output_dir / relative_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered)
        try:
            ast.parse(rendered)
            print(f"  ✓ {relative_path}")
        except SyntaxError as e:
            errors.append(f"  ✗ {relative_path}: line {e.lineno}: {e.msg}")
            print(f"  ✗ {relative_path}")

    return errors


def generate(result: ManifestResult, output_dir: Path) -> list[str]:
    vo_names = {vo.name for vo in result.value_objects}
    env = _make_env(vo_names)
    errors = []
    for agg in result.aggregates:
        target = output_dir / agg.name.lower()
        print(f"\n[{agg.name}] → {target}/")
        errors.extend(_generate_aggregate(agg, result.enums, result.value_objects, target, env))
    return errors

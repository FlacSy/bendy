import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader

from .merger import BlockParser, CodeBlock, CodeMerger, PrevGenerated
from .merger import render as _render_tree
from .state import BendyState, FileState
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
}

# soft-merge preserves user logic; everything else is fully regenerated
_SOFT_MERGE_TEMPLATES = {
    "app_use_cases.py.jinja",
    "router.py.jinja",
    "infra_repository.py.jinja",
}

# bump when a template's structure changes incompatibly (renamed methods, etc.)
_TEMPLATE_VERSIONS: dict[str, int] = {
    "domain_models.py.jinja": 1,
    "domain_repository.py.jinja": 1,
    "app_dtos.py.jinja": 1,
    "app_use_cases.py.jinja": 1,
    "router.py.jinja": 1,
    "infra_models.py.jinja": 1,
    "infra_repository.py.jinja": 1,
}

_DATETIME_IMPORTS = {"datetime", "date"}


@dataclass
class FileResult:
    relative_path: str
    status: Literal["new", "updated", "merged", "unchanged", "error"]
    error: str | None = None
    version_warning: str | None = None
    deleted_top: list[str] = field(default_factory=list)
    deleted_methods: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class AggregateResult:
    name: str
    output_dir: Path
    files: list[FileResult] = field(default_factory=list)


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


def _extract_file_state(gen_tree: list[CodeBlock], template_name: str) -> FileState:
    top_level_ids = [b.signature_id for b in gen_tree if b.type in ("class", "method")]
    per_class_ids: dict[str, list[str]] = {
        b.signature_id: [c.signature_id for c in b.child_blocks if c.type == "method"]
        for b in gen_tree
        if b.type == "class"
    }
    return FileState(
        template=template_name,
        template_version=_TEMPLATE_VERSIONS.get(template_name, 1),
        top_level_ids=top_level_ids,
        per_class_ids=per_class_ids,
    )


def _generate_aggregate(
    agg: AggregateInfo,
    all_enums: list[EnumInfo],
    all_vos: list[ValueObjectInfo],
    output_dir: Path,
    env: Environment,
    state: BendyState,
) -> list[FileResult]:
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

    agg_key = agg.name.lower()
    _parser = BlockParser()
    _merger = CodeMerger()
    results: list[FileResult] = []

    for template_name, relative_path in _AGGREGATE_TEMPLATES.items():
        rendered = env.get_template(template_name).render(**ctx)
        out_path = output_dir / relative_path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        gen_tree = _parser.parse(rendered)
        is_new = not out_path.exists()

        version_warning: str | None = None
        deleted_top: list[str] = []
        deleted_methods: dict[str, list[str]] = {}

        if not is_new and template_name in _SOFT_MERGE_TEMPLATES:
            prev_fs = state.get_file(agg_key, relative_path)

            current_ver = _TEMPLATE_VERSIONS.get(template_name, 1)
            if prev_fs and prev_fs.template_version != current_ver:
                version_warning = (
                    f"template version changed "
                    f"({prev_fs.template_version} → {current_ver}) — "
                    f"review merged output carefully"
                )

            prev: PrevGenerated | None = None
            if prev_fs:
                gen_top_ids = {b.signature_id for b in gen_tree if b.type in ("class", "method")}
                deleted_top = [sid for sid in prev_fs.top_level_ids if sid not in gen_top_ids]
                deleted_top_set = set(deleted_top)
                for cls_id, method_ids in prev_fs.per_class_ids.items():
                    # whole class deleted — don't enumerate its methods separately
                    if cls_id in deleted_top_set:
                        continue
                    gen_cls = next((b for b in gen_tree if b.signature_id == cls_id), None)
                    gen_method_ids = (
                        {c.signature_id for c in gen_cls.child_blocks if c.type == "method"}
                        if gen_cls
                        else set()
                    )
                    dropped = [m for m in method_ids if m not in gen_method_ids]
                    if dropped:
                        deleted_methods[cls_id] = dropped

                prev = PrevGenerated(
                    top_level=set(prev_fs.top_level_ids),
                    per_class={k: set(v) for k, v in prev_fs.per_class_ids.items()},
                )

            existing = out_path.read_text()
            merged_tree = _merger.merge(gen_tree, _parser.parse(existing), prev=prev)
            final = _render_tree(merged_tree)
            status: Literal["new", "updated", "merged", "unchanged", "error"] = (
                "unchanged" if final == existing else "merged"
            )
        else:
            existing = out_path.read_text() if not is_new else None
            final = rendered
            if is_new:
                status = "new"
            else:
                status = "unchanged" if final == existing else "updated"

        out_path.write_text(final)

        error: str | None = None
        try:
            ast.parse(final)
        except SyntaxError as e:
            error = f"line {e.lineno}: {e.msg}"
            status = "error"

        state.set_file(agg_key, relative_path, _extract_file_state(gen_tree, template_name))

        results.append(
            FileResult(
                relative_path=relative_path,
                status=status,
                error=error,
                version_warning=version_warning,
                deleted_top=deleted_top,
                deleted_methods=deleted_methods,
            )
        )

    return results


def generate(result: ManifestResult, output_dir: Path) -> list[AggregateResult]:
    vo_names = {vo.name for vo in result.value_objects}
    env = _make_env(vo_names)

    bendy_dir = output_dir / ".bendy"
    bendy_dir.mkdir(parents=True, exist_ok=True)
    state_path = bendy_dir / "state.json"
    state = BendyState.load(state_path)

    agg_results: list[AggregateResult] = []
    for agg in result.aggregates:
        target = output_dir / agg.name.lower()
        files = _generate_aggregate(agg, result.enums, result.value_objects, target, env, state)
        agg_results.append(AggregateResult(name=agg.name, output_dir=target, files=files))

    state.save(state_path)
    return agg_results

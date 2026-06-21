import sys
from pathlib import Path

from .generator import _AGGREGATE_TEMPLATES, generate
from .reader import read_manifest
from .validator import validate


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: bendy <manifest.py> [output_dir]")
        sys.exit(0 if sys.argv[1:] else 1)

    manifest_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")

    result = read_manifest(manifest_path)

    if not result.aggregates and result.errors:
        for e in result.errors:
            print(f"error: {e}")
        sys.exit(1)

    if not result.aggregates:
        print("no aggregates found")
        sys.exit(1)

    names = ", ".join(a.name for a in result.aggregates)
    print(f"{manifest_path}: {len(result.aggregates)} aggregate(s) — {names}")
    if result.enums:
        print(f"  enums: {', '.join(e.name for e in result.enums)}")
    if result.value_objects:
        print(f"  value objects: {', '.join(v.name for v in result.value_objects)}")

    errors = result.errors + validate(result)
    if errors:
        for e in errors:
            print(e)
        sys.exit(1)

    gen_errors = generate(result, output_dir)
    if gen_errors:
        for e in gen_errors:
            print(e)
        sys.exit(1)

    total = len(result.aggregates) * len(_AGGREGATE_TEMPLATES)
    print(f"\n{total} files generated in {output_dir}/")

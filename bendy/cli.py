import sys
import time
from pathlib import Path

from rich.console import Console
from rich.text import Text

from .generator import FileResult, generate
from .reader import read_manifest
from .state import BENDYKIT_VERSION
from .validator import validate

_con = Console(highlight=False)
_err = Console(stderr=True, highlight=False)

# (color, icon, label)
_STATUS: dict[str, tuple[str, str, str]] = {
    "new": ("green", "+", "new      "),
    "updated": ("cyan", "↺", "updated  "),
    "merged": ("yellow", "~", "merged   "),
    "unchanged": ("dim", "·", "unchanged"),
    "error": ("red", "✗", "error    "),
}


def _id_to_name(sig_id: str) -> str:
    """'class:Foo' → 'Foo',  'method:bar' → 'bar'"""
    return sig_id.split(":", 1)[-1] if ":" in sig_id else sig_id


def _print_help() -> None:
    _con.print(f"\n  [bold]bendykit[/bold] [dim]v{BENDYKIT_VERSION}[/dim]")
    _con.print()
    _con.print("  DDD scaffold generator for Python backends.")
    _con.print("  [dim]FastAPI · SQLAlchemy · Pydantic · Onion Architecture[/dim]")
    _con.print()
    _con.print("  [bold]Usage[/bold]")
    _con.print("    bendykit [cyan]<manifest.py>[/cyan] [[green]output_dir[/green]]")
    _con.print()
    _con.print("  [bold]Arguments[/bold]")
    _con.print("    [cyan]manifest.py[/cyan]   Aggregate manifest file (Python DSL)")
    _con.print(
        "    [green]output_dir[/green]    Output directory  [dim](default: current dir)[/dim]"
    )
    _con.print()
    _con.print("  [bold]Options[/bold]")
    _con.print("    -h, --help    Show this message and exit")
    _con.print()
    _con.print("  [bold]Example manifest[/bold]")
    _con.print("    [dim]from bendy import Aggregate[/dim]")
    _con.print()
    _con.print("    [dim]class Order(Aggregate):[/dim]")
    _con.print("    [dim]    total: float[/dim]")
    _con.print("    [dim]    paid: bool = False[/dim]")
    _con.print()
    _con.print("    [dim]    class Meta:[/dim]")
    _con.print('    [dim]        use_cases = ["create", "get", "update"][/dim]')
    _con.print()


def _print_file(fr: FileResult) -> None:
    color, icon, label = _STATUS[fr.status]
    is_dim = fr.status == "unchanged"

    line = Text("    ")
    line.append(f"{icon}  ", style=color)
    line.append(label + "  ", style="dim" if is_dim else color)
    line.append(fr.relative_path, style="dim" if is_dim else "")
    _con.print(line)

    if fr.version_warning:
        _con.print(f"         [yellow]⚠  {fr.relative_path}: {fr.version_warning}[/yellow]")

    if fr.error:
        _con.print(f"         [red]{fr.error}[/red]")

    for sid in fr.deleted_top:
        _con.print(f"         [red dim]↳ deleted  {_id_to_name(sid)}[/red dim]")

    for cls_id, method_ids in fr.deleted_methods.items():
        cls_name = _id_to_name(cls_id)
        for mid in method_ids:
            _con.print(f"         [red dim]↳ deleted  {cls_name}.{_id_to_name(mid)}[/red dim]")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _print_help()
        sys.exit(0 if sys.argv[1:] else 1)

    manifest_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")

    t_start = time.monotonic()

    result = read_manifest(manifest_path)

    if not result.aggregates and result.errors:
        for e in result.errors:
            _err.print(f"[red]error:[/red] {e}")
        sys.exit(1)

    if not result.aggregates:
        _err.print("[red]error:[/red] no aggregates found")
        sys.exit(1)

    errors = result.errors + validate(result)
    if errors:
        for e in errors:
            _err.print(f"[red]error:[/red] {e}")
        sys.exit(1)

    # ── header ────────────────────────────────────────────────────────────────
    names = ", ".join(a.name for a in result.aggregates)
    n = len(result.aggregates)
    _con.print()
    _con.print(f"  [bold]{manifest_path}[/bold]  ·  {n} aggregate{'s' if n != 1 else ''} — {names}")
    if result.enums:
        _con.print(f"  [dim]enums: {', '.join(e.name for e in result.enums)}[/dim]")
    if result.value_objects:
        _con.print(f"  [dim]value objects: {', '.join(v.name for v in result.value_objects)}[/dim]")
    _con.print()

    # ── generate ──────────────────────────────────────────────────────────────
    agg_results = generate(result, output_dir)
    elapsed = time.monotonic() - t_start

    # ── per-aggregate display ─────────────────────────────────────────────────
    counts: dict[str, int] = {"new": 0, "updated": 0, "merged": 0, "unchanged": 0, "error": 0}
    deleted_count = 0
    has_errors = False

    for ar in agg_results:
        _con.print(f"  [bold]{ar.name}[/bold]  →  {ar.output_dir}/")
        for fr in ar.files:
            _print_file(fr)
            counts[fr.status] += 1
            deleted_count += len(fr.deleted_top)
            for v in fr.deleted_methods.values():
                deleted_count += len(v)
            if fr.status == "error":
                has_errors = True
        _con.print()

    # ── summary ───────────────────────────────────────────────────────────────
    total = sum(len(ar.files) for ar in agg_results)

    parts: list[str] = []
    if counts["new"]:
        parts.append(f"[green]{counts['new']} new[/green]")
    if counts["updated"]:
        parts.append(f"[cyan]{counts['updated']} updated[/cyan]")
    if counts["merged"]:
        parts.append(f"[yellow]{counts['merged']} merged[/yellow]")
    if counts["unchanged"]:
        parts.append(f"[dim]{counts['unchanged']} unchanged[/dim]")
    if deleted_count:
        parts.append(f"[red]{deleted_count} deleted[/red]")
    if counts["error"]:
        n_err = counts["error"]
        parts.append(f"[bold red]{n_err} error{'s' if n_err != 1 else ''}[/bold red]")

    stats = "  ·  ".join(parts)
    _con.print(
        f"  [bold]{total} files generated[/bold] in {output_dir}/"
        f"  ·  {stats}"
        f"  ·  [dim]{elapsed:.2f}s[/dim]"
    )
    _con.print()

    if has_errors:
        sys.exit(1)

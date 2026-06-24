"""
Tests for:
  1. BendyState load/save round-trip
  2. CodeMerger deletion via PrevGenerated
  3. Template version warnings in generator
  4. Full E2E: class deleted from manifest disappears from output
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

from bendy.merger import BlockParser, CodeMerger, PrevGenerated, render
from bendy.state import SCHEMA_VERSION, BendyState, FileState

_MAIN = Path(__file__).parent.parent / "main.py"
_parser = BlockParser()
_merger = CodeMerger()


# ─── Helpers ──────────────────────────────────────────────────────────────────


def run_cli(manifest: Path, output: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_MAIN), str(manifest), str(output)],
        capture_output=True,
        text=True,
        cwd=str(_MAIN.parent),
    )


def parse(text: str):
    return _parser.parse(text)


def merge(gen: str, user: str, prev: PrevGenerated | None = None) -> str:
    return render(_merger.merge(parse(gen), parse(user), prev=prev))


# ─── BendyState ───────────────────────────────────────────────────────────────


class TestBendyState:
    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        state = BendyState.load(tmp_path / "nonexistent.json")
        assert state.schema_version == SCHEMA_VERSION
        assert state.aggregates == {}

    def test_load_corrupt_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        p.write_text("not json {{{")
        state = BendyState.load(p)
        assert state.aggregates == {}

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        state = BendyState()
        state.set_file(
            "task",
            "application/use_cases.py",
            FileState(
                template="app_use_cases.py.jinja",
                template_version=1,
                top_level_ids=["class:CreateTaskUseCase", "class:GetTaskUseCase"],
                per_class_ids={
                    "class:CreateTaskUseCase": ["method:__init__", "method:execute"],
                    "class:GetTaskUseCase": ["method:__init__", "method:execute"],
                },
            ),
        )
        state.save(p)

        loaded = BendyState.load(p)
        fs = loaded.get_file("task", "application/use_cases.py")
        assert fs is not None
        assert fs.template == "app_use_cases.py.jinja"
        assert fs.template_version == 1
        assert "class:CreateTaskUseCase" in fs.top_level_ids
        assert "method:execute" in fs.per_class_ids["class:CreateTaskUseCase"]

    def test_save_produces_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        state = BendyState()
        state.set_file(
            "order",
            "domain/models.py",
            FileState(
                template="domain_models.py.jinja",
                template_version=1,
                top_level_ids=["class:Order"],
                per_class_ids={},
            ),
        )
        state.save(p)
        raw = json.loads(p.read_text())
        assert raw["schema_version"] == SCHEMA_VERSION
        assert "order" in raw["aggregates"]

    def test_saved_ids_are_sorted(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        state = BendyState()
        state.set_file(
            "task",
            "application/use_cases.py",
            FileState(
                template="app_use_cases.py.jinja",
                template_version=1,
                top_level_ids=["class:GetTaskUseCase", "class:CreateTaskUseCase"],
                per_class_ids={
                    "class:GetTaskUseCase": ["method:execute", "method:__init__"],
                },
            ),
        )
        state.save(p)
        raw = json.loads(p.read_text())
        ids = raw["aggregates"]["task"]["application/use_cases.py"]["top_level_ids"]
        assert ids == sorted(ids)

    def test_get_file_missing_returns_none(self) -> None:
        state = BendyState()
        assert state.get_file("task", "application/use_cases.py") is None

    def test_set_file_creates_aggregate_key(self) -> None:
        state = BendyState()
        state.set_file(
            "order",
            "domain/models.py",
            FileState(
                template="domain_models.py.jinja",
                template_version=1,
                top_level_ids=[],
                per_class_ids={},
            ),
        )
        assert "order" in state.aggregates
        assert "domain/models.py" in state.aggregates["order"]

    def test_generated_at_set_on_save(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        state = BendyState()
        state.save(p)
        raw = json.loads(p.read_text())
        assert raw["generated_at"] != ""


# ─── Merger: deletion ─────────────────────────────────────────────────────────


class TestMergerDeletion:
    def test_stale_class_removed_from_output(self) -> None:
        """A class that was generated before but removed from the template should
        be deleted from the output, even if the user never touched it."""
        gen = """\
class CreateTaskUseCase:
    def __init__(self) -> None:
        pass
"""
        user = """\
class CreateTaskUseCase:
    def __init__(self) -> None:
        pass

class DeleteTaskUseCase:
    def __init__(self) -> None:
        pass
"""
        prev = PrevGenerated(
            top_level={"class:CreateTaskUseCase", "class:DeleteTaskUseCase"},
            per_class={},
        )
        result = merge(gen, user, prev=prev)
        assert "class:CreateTaskUseCase" not in result or "CreateTaskUseCase" in result
        assert "DeleteTaskUseCase" not in result

    def test_user_only_class_preserved_without_prev(self) -> None:
        """Without prev info, user-only classes must NEVER be deleted."""
        gen = """\
class CreateTaskUseCase:
    def __init__(self) -> None:
        pass
"""
        user = """\
class CreateTaskUseCase:
    def __init__(self) -> None:
        pass

class MyCustomHelper:
    def do_thing(self) -> None:
        pass
"""
        result = merge(gen, user, prev=None)
        assert "MyCustomHelper" in result

    def test_user_only_class_preserved_when_not_in_prev(self) -> None:
        """A class the user wrote themselves (not in prev) must survive deletion."""
        gen = """\
class CreateTaskUseCase:
    def __init__(self) -> None:
        pass
"""
        user = """\
class CreateTaskUseCase:
    def __init__(self) -> None:
        pass

class MyCustomHelper:
    def do_thing(self) -> None:
        pass
"""
        # MyCustomHelper is NOT in prev → user-written → must be kept
        prev = PrevGenerated(
            top_level={"class:CreateTaskUseCase"},
            per_class={},
        )
        result = merge(gen, user, prev=prev)
        assert "MyCustomHelper" in result

    def test_stale_method_removed_from_class(self) -> None:
        """A method that was previously generated but removed from the template
        should be deleted even if the user has it in the file."""
        gen = """\
class TaskUseCase:
    def __init__(self) -> None:
        pass

    def execute(self) -> None:
        pass
"""
        user = """\
class TaskUseCase:
    def __init__(self) -> None:
        pass

    def execute(self) -> None:
        pass

    def old_generated_method(self) -> None:
        pass
"""
        prev = PrevGenerated(
            top_level={"class:TaskUseCase"},
            per_class={
                "class:TaskUseCase": {
                    "method:__init__",
                    "method:execute",
                    "method:old_generated_method",  # was generated, now removed
                },
            },
        )
        result = merge(gen, user, prev=prev)
        assert "old_generated_method" not in result

    def test_user_method_preserved_when_not_in_prev(self) -> None:
        """A method the user added manually (not in prev) must survive."""
        gen = """\
class TaskUseCase:
    def __init__(self) -> None:
        pass

    def execute(self) -> None:
        pass
"""
        user = """\
class TaskUseCase:
    def __init__(self) -> None:
        pass

    def execute(self) -> None:
        pass

    def _my_helper(self) -> str:
        return "custom"
"""
        prev = PrevGenerated(
            top_level={"class:TaskUseCase"},
            per_class={
                "class:TaskUseCase": {"method:__init__", "method:execute"},
                # _my_helper NOT in prev → user-written → preserve
            },
        )
        result = merge(gen, user, prev=prev)
        assert "_my_helper" in result
        assert 'return "custom"' in result

    def test_stale_top_level_function_removed(self) -> None:
        """Top-level functions (e.g. route handlers) that are stale get removed."""
        gen = """\
def create_task():
    pass
"""
        user = """\
def create_task():
    pass

def delete_task():
    pass
"""
        prev = PrevGenerated(
            top_level={"method:create_task", "method:delete_task"},
            per_class={},
        )
        result = merge(gen, user, prev=prev)
        assert "delete_task" not in result

    def test_user_top_level_function_preserved(self) -> None:
        """User-written top-level function (not in prev) is kept."""
        gen = """\
def create_task():
    pass
"""
        user = """\
def create_task():
    pass

def my_utility():
    return 42
"""
        prev = PrevGenerated(
            top_level={"method:create_task"},
            per_class={},
        )
        result = merge(gen, user, prev=prev)
        assert "my_utility" in result

    def test_no_prev_preserves_everything(self) -> None:
        """Without prev data, all user content must be preserved (safe default)."""
        gen = """\
class A:
    def method_a(self) -> None:
        pass
"""
        user = """\
class A:
    def method_a(self) -> None:
        pass

class B:
    def method_b(self) -> None:
        pass
"""
        result = merge(gen, user, prev=None)
        assert "class A:" in result
        assert "class B:" in result


# ─── Template version warning (E2E via CLI stdout) ────────────────────────────


class TestTemplateVersionWarning:
    def test_no_warning_on_first_run(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.py"
        manifest.write_text("""\
from bendy import Aggregate

class Task(Aggregate):
    title: str

    class Meta:
        use_cases = ["create"]
""")
        proc = run_cli(manifest, tmp_path / "out")
        assert proc.returncode == 0
        assert "⚠" not in proc.stdout

    def test_no_warning_on_same_version_reruns(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.py"
        manifest.write_text("""\
from bendy import Aggregate

class Task(Aggregate):
    title: str

    class Meta:
        use_cases = ["create"]
""")
        out = tmp_path / "out"
        run_cli(manifest, out)
        proc2 = run_cli(manifest, out)
        assert proc2.returncode == 0
        assert "⚠" not in proc2.stdout

    def test_version_warning_when_state_has_old_version(self, tmp_path: Path) -> None:
        """Manually write a state file with an old template version, then
        regenerate — the CLI must print a ⚠ warning."""
        manifest = tmp_path / "manifest.py"
        manifest.write_text("""\
from bendy import Aggregate

class Task(Aggregate):
    title: str

    class Meta:
        use_cases = ["create"]
""")
        out = tmp_path / "out"
        # First run — creates state file
        run_cli(manifest, out)

        # Tamper: set template version to 0 for the soft-merged file
        state_path = out / ".bendy/state.json"
        raw = json.loads(state_path.read_text())
        raw["aggregates"]["task"]["application/use_cases.py"]["template_version"] = 0
        state_path.write_text(json.dumps(raw, indent=2))

        # Second run — must warn
        proc2 = run_cli(manifest, out)
        assert proc2.returncode == 0
        assert "⚠" in proc2.stdout
        assert "use_cases.py" in proc2.stdout


# ─── E2E: deletion via manifest change ────────────────────────────────────────

MANIFEST_WITH_DELETE = """\
from bendy import Aggregate

class Task(Aggregate):
    title: str

    class Meta:
        use_cases = ["create", "delete"]
"""

MANIFEST_DELETE_REMOVED = """\
from bendy import Aggregate

class Task(Aggregate):
    title: str

    class Meta:
        use_cases = ["create"]
"""


class TestE2EDeletion:
    def test_removed_use_case_class_deleted_from_file(self, tmp_path: Path) -> None:
        """When a use case is removed from the manifest, its generated class
        should disappear from use_cases.py on the next run."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        use_cases = out / "task/application/use_cases.py"

        manifest.write_text(MANIFEST_WITH_DELETE)
        proc1 = run_cli(manifest, out)
        assert proc1.returncode == 0
        assert "DeleteTaskUseCase" in use_cases.read_text()

        manifest.write_text(MANIFEST_DELETE_REMOVED)
        proc2 = run_cli(manifest, out)
        assert proc2.returncode == 0
        assert "DeleteTaskUseCase" not in use_cases.read_text()

        # Remaining class still there
        assert "CreateTaskUseCase" in use_cases.read_text()

        # File is still valid Python
        ast.parse(use_cases.read_text())

    def test_user_written_class_survives_manifest_change(self, tmp_path: Path) -> None:
        """User-added classes (not in prev state) must never be deleted."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        use_cases = out / "task/application/use_cases.py"

        manifest.write_text(MANIFEST_WITH_DELETE)
        run_cli(manifest, out)

        # User adds a hand-written helper class
        current = use_cases.read_text()
        use_cases.write_text(current + "\n\nclass MyHelper:\n    pass\n")

        manifest.write_text(MANIFEST_DELETE_REMOVED)
        run_cli(manifest, out)

        result = use_cases.read_text()
        assert "MyHelper" in result
        ast.parse(result)

    def test_state_file_created_after_generation(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.py"
        manifest.write_text("""\
from bendy import Aggregate

class Task(Aggregate):
    title: str

    class Meta:
        use_cases = ["create"]
""")
        out = tmp_path / "out"
        proc = run_cli(manifest, out)
        assert proc.returncode == 0
        state_path = out / ".bendy/state.json"
        assert state_path.exists()
        raw = json.loads(state_path.read_text())
        assert "task" in raw["aggregates"]
        assert "application/use_cases.py" in raw["aggregates"]["task"]

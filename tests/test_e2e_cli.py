"""
End-to-end CLI test: full generate → user-edit → re-generate lifecycle.

The test runs `python main.py <manifest> <output>` as a subprocess three times
and checks the filesystem state after each run.

Phase 1 — First generation
    Manifest v1 (Task: title, done).
    Assert all 7 files exist, contain correct classes/fields, are valid Python.

Phase 2 — User edits + idempotent re-generation (same manifest)
    User writes business logic into use_cases.py (custom validation + helper method).
    Regenerate with the same manifest.
    Assert:
      - user's body code and helper method are preserved
      - class names and method signatures are intact
      - all files are valid Python

Phase 3 — Manifest update: new field added
    Manifest v2 adds `priority: int = 0`.
    Regenerate.
    Assert:
      - domain model and DTOs (generator-controlled) have `priority`
      - use_cases.py (soft-merged) still contains the user's custom body
      - router.py and infra_repository.py are soft-merged (user code survives)
      - all files are valid Python
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

# ─── constants ────────────────────────────────────────────────────────────────

_MAIN = Path(__file__).parent.parent / "main.py"

MANIFEST_V1 = """\
from bendy import Aggregate

class Task(Aggregate):
    title: str
    done: bool = False

    class Meta:
        use_cases = ["create", "get"]
"""

MANIFEST_V2 = """\
from bendy import Aggregate

class Task(Aggregate):
    title: str
    priority: int = 0
    done: bool = False

    class Meta:
        use_cases = ["create", "get"]
"""

# What the user writes into use_cases.py after first generation.
# Differs from generated output in two ways:
#   1. execute() body has custom validation logic (comment + ValueError)
#   2. CreateTaskUseCase has a user-added helper method _validate_title()
USER_EDITED_USE_CASES = """\
from uuid import UUID

from ..domain.models import Task
from ..domain.repository import TaskRepository
from .dtos import (
    TaskCreate,
    TaskResponse,
)


class CreateTaskUseCase:
    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    async def execute(self, data: TaskCreate) -> TaskResponse:
        # Custom: strip whitespace and validate before persisting
        title = data.title.strip()
        if not title:
            raise ValueError("Title cannot be empty")
        entity = Task(
            title=title,
            done=data.done,
        )
        await self._repository.save(entity)
        return TaskResponse.model_validate(entity)

    def _validate_title(self, title: str) -> str:
        \"\"\"User-added helper — must survive re-generation.\"\"\"
        return title.strip()


class GetTaskUseCase:
    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    async def execute(self, id: UUID) -> TaskResponse:
        entity = await self._repository.get_by_id(id)
        if entity is None:
            raise ValueError(f"Task {id} not found")
        return TaskResponse.model_validate(entity)
"""

# ─── helpers ──────────────────────────────────────────────────────────────────


def run_cli(manifest: Path, output: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_MAIN), str(manifest), str(output)],
        capture_output=True,
        text=True,
        cwd=str(_MAIN.parent),
    )


def assert_valid_python(path: Path) -> None:
    src = path.read_text()
    try:
        ast.parse(src)
    except SyntaxError as exc:
        pytest.fail(f"SyntaxError in {path.name}: {exc}\n\n{src}")


def assert_all_files_valid(output: Path) -> None:
    py_files = list(output.rglob("*.py"))
    assert py_files, "No .py files found under output directory"
    for f in py_files:
        assert_valid_python(f)


# ─── e2e test ─────────────────────────────────────────────────────────────────


def test_cli_full_lifecycle(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.py"
    output = tmp_path / "out"

    # paths we'll inspect in every phase
    task_dir = output / "task"
    models_path = task_dir / "domain/models.py"
    dtos_path = task_dir / "application/dtos.py"
    use_cases_path = task_dir / "application/use_cases.py"
    router_path = task_dir / "presentation/router.py"
    infra_repo_path = task_dir / "infrastructure/repository.py"

    # ── Phase 1: first generation ─────────────────────────────────────────
    manifest.write_text(MANIFEST_V1)
    proc = run_cli(manifest, output)

    assert proc.returncode == 0, f"CLI failed:\n{proc.stderr}"
    assert "7 files generated" in proc.stdout

    # All expected files exist
    for path in [models_path, dtos_path, use_cases_path, router_path, infra_repo_path]:
        assert path.exists(), f"Expected file missing: {path.relative_to(output)}"

    # domain/models.py — @dataclass with correct fields
    models = models_path.read_text()
    assert "@dataclass" in models
    assert "class Task:" in models
    assert "title: str" in models
    assert "done: bool = False" in models
    assert "id: UUID = field(default_factory=uuid4)" in models

    # application/dtos.py — Pydantic models, no priority yet
    dtos = dtos_path.read_text()
    assert "class TaskCreate(BaseModel):" in dtos
    assert "class TaskResponse(BaseModel):" in dtos
    assert "title: str" in dtos
    assert "done: bool = False" in dtos
    assert "priority" not in dtos

    # application/use_cases.py — two use case classes, plain bodies
    uc = use_cases_path.read_text()
    assert "class CreateTaskUseCase:" in uc
    assert "class GetTaskUseCase:" in uc
    assert "async def execute" in uc
    assert "TaskResponse.model_validate(entity)" in uc
    # No user code yet
    assert "Custom:" not in uc
    assert "_validate_title" not in uc

    # presentation/router.py — FastAPI routes
    router = router_path.read_text()
    assert '@router.post("/"' in router
    assert '@router.get("/{id}"' in router
    assert "create_task" in router
    assert "get_task" in router

    # All generated files parse as valid Python
    assert_all_files_valid(output)

    # ── Phase 2: user edits use_cases.py, then regenerates (same manifest) ─
    use_cases_path.write_text(USER_EDITED_USE_CASES)

    proc2 = run_cli(manifest, output)
    assert proc2.returncode == 0, f"CLI failed on re-gen:\n{proc2.stderr}"

    uc2 = use_cases_path.read_text()

    # User's custom comment and validation logic preserved
    assert "# Custom: strip whitespace and validate before persisting" in uc2
    assert 'raise ValueError("Title cannot be empty")' in uc2

    # User-added helper method preserved
    assert "def _validate_title(self, title: str) -> str:" in uc2
    assert "User-added helper" in uc2

    # Generator-owned structure is still correct
    assert "class CreateTaskUseCase:" in uc2
    assert "class GetTaskUseCase:" in uc2
    assert "async def execute" in uc2

    # generator-controlled files are NOT affected by soft-update
    # (they must be identical to what was generated in Phase 1)
    assert models_path.read_text() == models
    assert dtos_path.read_text() == dtos

    assert_all_files_valid(output)

    # ── Phase 3: manifest v2 — new field, DTOs update, user body survives ──
    manifest.write_text(MANIFEST_V2)

    proc3 = run_cli(manifest, output)
    assert proc3.returncode == 0, f"CLI failed on v2 gen:\n{proc3.stderr}"

    # Generator-controlled files fully regenerated with new field
    models_v2 = models_path.read_text()
    assert "priority: int = 0" in models_v2

    dtos_v2 = dtos_path.read_text()
    assert "priority: int = 0" in dtos_v2

    # Soft-merged file: user's body code survived the header update
    uc3 = use_cases_path.read_text()

    # User's custom logic still present
    assert "# Custom: strip whitespace and validate before persisting" in uc3
    assert 'raise ValueError("Title cannot be empty")' in uc3
    assert "def _validate_title(self, title: str) -> str:" in uc3

    # Correct class/method structure
    assert "class CreateTaskUseCase:" in uc3
    assert "class GetTaskUseCase:" in uc3
    assert "async def execute" in uc3

    # New import (TaskCreate now also has priority, but import line stays same)
    assert "from ..domain.models import Task" in uc3
    assert "from ..domain.repository import TaskRepository" in uc3

    # Everything is syntactically valid Python after the merge
    assert_all_files_valid(output)


def test_cli_exits_nonzero_on_bad_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.py"
    manifest.write_text("this is not valid python ^^^")
    proc = run_cli(manifest, tmp_path / "out")
    assert proc.returncode != 0


def test_cli_exits_nonzero_on_missing_manifest(tmp_path: Path) -> None:
    proc = run_cli(tmp_path / "nonexistent.py", tmp_path / "out")
    assert proc.returncode != 0


def test_cli_help_exits_zero() -> None:
    proc = subprocess.run(
        [sys.executable, str(_MAIN), "--help"],
        capture_output=True,
        text=True,
        cwd=str(_MAIN.parent),
    )
    assert proc.returncode == 0
    assert "Usage" in proc.stdout


def test_cli_two_aggregates_each_get_own_directory(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.py"
    manifest.write_text("""\
from bendy import Aggregate

class User(Aggregate):
    email: str

class Order(Aggregate):
    total: float
""")
    proc = run_cli(manifest, tmp_path / "out")
    assert proc.returncode == 0
    assert (tmp_path / "out/user/domain/models.py").exists()
    assert (tmp_path / "out/order/domain/models.py").exists()
    assert_all_files_valid(tmp_path / "out")

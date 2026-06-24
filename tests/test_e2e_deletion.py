"""
E2E deletion tests: verify that blocks removed from the manifest disappear from
generated files on the next run, while user-written content is always preserved.

Three soft-merge files are covered independently:
  application/use_cases.py  — class-level deletion (use case classes)
  presentation/router.py    — top-level function deletion (route handlers)
  infrastructure/repository.py — method-level deletion (list() inside the repo class)

Every test follows the same three-phase pattern:
  1. Generate with the FULL manifest (all use cases).
  2. (Optional) The user writes custom code.
  3. Regenerate with a REDUCED manifest (some use cases removed).
  4. Assert stale generated blocks are gone; user content is intact.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

_MAIN = Path(__file__).parent.parent / "main.py"


# ─── Manifests ────────────────────────────────────────────────────────────────

# All five use cases
MANIFEST_FULL = dedent("""\
    from bendy import Aggregate

    class Task(Aggregate):
        title: str
        done: bool = False

        class Meta:
            use_cases = ["create", "get", "update", "delete", "list"]
""")

# Only create + get (removes update, delete, list)
MANIFEST_CREATE_GET = dedent("""\
    from bendy import Aggregate

    class Task(Aggregate):
        title: str
        done: bool = False

        class Meta:
            use_cases = ["create", "get"]
""")

# Only create (removes everything else)
MANIFEST_CREATE_ONLY = dedent("""\
    from bendy import Aggregate

    class Task(Aggregate):
        title: str
        done: bool = False

        class Meta:
            use_cases = ["create"]
""")

# create + list (for repo.list() deletion tests)
MANIFEST_WITH_LIST = dedent("""\
    from bendy import Aggregate

    class Task(Aggregate):
        title: str

        class Meta:
            use_cases = ["create", "list"]
""")

MANIFEST_WITHOUT_LIST = dedent("""\
    from bendy import Aggregate

    class Task(Aggregate):
        title: str

        class Meta:
            use_cases = ["create"]
""")


# ─── Helpers ──────────────────────────────────────────────────────────────────


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
        pytest.fail(f"SyntaxError in {path.name}:\n{exc}\n\n{src}")


def paths(out: Path) -> dict[str, Path]:
    base = out / "task"
    return {
        "use_cases": base / "application/use_cases.py",
        "router": base / "presentation/router.py",
        "infra": base / "infrastructure/repository.py",
        "state": out / ".bendy/state.json",
    }


# ─── use_cases.py ─────────────────────────────────────────────────────────────


class TestUseCasesDeletion:
    def test_stale_use_case_classes_removed(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        # Phase 1: generate all five use cases
        manifest.write_text(MANIFEST_FULL)
        proc1 = run_cli(manifest, out)
        assert proc1.returncode == 0, proc1.stderr

        uc1 = p["use_cases"].read_text()
        assert "class CreateTaskUseCase:" in uc1
        assert "class GetTaskUseCase:" in uc1
        assert "class UpdateTaskUseCase:" in uc1
        assert "class DeleteTaskUseCase:" in uc1
        assert "class ListTasksUseCase:" in uc1

        # Phase 2: reduce manifest — keep only create + get
        manifest.write_text(MANIFEST_CREATE_GET)
        proc2 = run_cli(manifest, out)
        assert proc2.returncode == 0, proc2.stderr

        uc2 = p["use_cases"].read_text()
        assert "class CreateTaskUseCase:" in uc2
        assert "class GetTaskUseCase:" in uc2
        # Removed from template → must be gone
        assert "class UpdateTaskUseCase:" not in uc2
        assert "class DeleteTaskUseCase:" not in uc2
        assert "class ListTasksUseCase:" not in uc2

        assert_valid_python(p["use_cases"])

    def test_further_reduction_removes_more_classes(self, tmp_path: Path) -> None:
        """Three-step reduction: full → create+get → create only."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_FULL)
        run_cli(manifest, out)

        manifest.write_text(MANIFEST_CREATE_GET)
        run_cli(manifest, out)

        manifest.write_text(MANIFEST_CREATE_ONLY)
        proc3 = run_cli(manifest, out)
        assert proc3.returncode == 0, proc3.stderr

        uc3 = p["use_cases"].read_text()
        assert "class CreateTaskUseCase:" in uc3
        assert "class GetTaskUseCase:" not in uc3

        assert_valid_python(p["use_cases"])

    def test_user_body_in_remaining_class_preserved(self, tmp_path: Path) -> None:
        """The user's custom body inside a class that *stays* must survive deletion."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_FULL)
        run_cli(manifest, out)

        # User writes custom logic into the execute() of CreateTaskUseCase
        current = p["use_cases"].read_text()
        custom_body = current.replace(
            "        entity = Task(\n"
            "            title=data.title,\n"
            "            done=data.done,\n"
            "        )\n"
            "        await self._repository.save(entity)\n"
            "        return TaskResponse.model_validate(entity)",
            "        # custom: validate title length\n"
            "        if len(data.title) > 100:\n"
            '            raise ValueError("Title too long")\n'
            "        entity = Task(\n"
            "            title=data.title,\n"
            "            done=data.done,\n"
            "        )\n"
            "        await self._repository.save(entity)\n"
            "        return TaskResponse.model_validate(entity)",
        )
        p["use_cases"].write_text(custom_body)

        # Regenerate with fewer use cases
        manifest.write_text(MANIFEST_CREATE_GET)
        proc2 = run_cli(manifest, out)
        assert proc2.returncode == 0, proc2.stderr

        uc2 = p["use_cases"].read_text()
        assert "# custom: validate title length" in uc2
        assert 'raise ValueError("Title too long")' in uc2
        # Stale classes gone
        assert "class UpdateTaskUseCase:" not in uc2
        assert "class DeleteTaskUseCase:" not in uc2

        assert_valid_python(p["use_cases"])

    def test_user_added_class_not_in_prev_always_preserved(self, tmp_path: Path) -> None:
        """A class the user wrote by hand (never generated) must never be deleted."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_FULL)
        run_cli(manifest, out)

        # User appends a completely custom class
        p["use_cases"].write_text(
            p["use_cases"].read_text()
            + dedent("""\


            class MyNotificationService:
                \"\"\"Hand-written by user — must never be deleted.\"\"\"

                def notify(self, msg: str) -> None:
                    print(msg)
            """)
        )

        manifest.write_text(MANIFEST_CREATE_GET)
        run_cli(manifest, out)

        uc = p["use_cases"].read_text()
        assert "class MyNotificationService:" in uc
        assert "Hand-written by user" in uc
        assert "class DeleteTaskUseCase:" not in uc

        assert_valid_python(p["use_cases"])

    def test_user_added_method_in_remaining_class_preserved(self, tmp_path: Path) -> None:
        """User-added helper method inside a class that stays must be kept."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_FULL)
        run_cli(manifest, out)

        # User adds a private helper to CreateTaskUseCase
        current = p["use_cases"].read_text()
        # Find the end of CreateTaskUseCase.execute and insert a helper after it
        insert_after = "        return TaskResponse.model_validate(entity)"
        helper = dedent("""\

            def _sanitize(self, value: str) -> str:
                return value.strip().lower()
        """)
        # Indent to class level (4 spaces)
        helper_indented = "\n" + "\n".join("    " + line for line in helper.strip().splitlines())
        p["use_cases"].write_text(
            current.replace(
                insert_after,
                insert_after + helper_indented,
                1,  # only first occurrence (CreateTaskUseCase.execute)
            )
        )

        manifest.write_text(MANIFEST_CREATE_GET)
        run_cli(manifest, out)

        uc = p["use_cases"].read_text()
        assert "def _sanitize(self, value: str) -> str:" in uc
        assert "value.strip().lower()" in uc
        assert "class DeleteTaskUseCase:" not in uc

        assert_valid_python(p["use_cases"])

    def test_state_file_updated_after_deletion(self, tmp_path: Path) -> None:
        """State file must no longer list deleted class IDs after regen."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_FULL)
        run_cli(manifest, out)

        raw1 = json.loads(p["state"].read_text())
        ids1 = raw1["aggregates"]["task"]["application/use_cases.py"]["top_level_ids"]
        assert "class:DeleteTaskUseCase" in ids1

        manifest.write_text(MANIFEST_CREATE_GET)
        run_cli(manifest, out)

        raw2 = json.loads(p["state"].read_text())
        ids2 = raw2["aggregates"]["task"]["application/use_cases.py"]["top_level_ids"]
        assert "class:DeleteTaskUseCase" not in ids2
        assert "class:CreateTaskUseCase" in ids2
        assert "class:GetTaskUseCase" in ids2


# ─── router.py ────────────────────────────────────────────────────────────────


class TestRouterDeletion:
    def test_stale_route_handlers_removed(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_FULL)
        proc1 = run_cli(manifest, out)
        assert proc1.returncode == 0, proc1.stderr

        r1 = p["router"].read_text()
        assert "async def list_tasks" in r1
        assert "async def create_task" in r1
        assert "async def get_task" in r1
        assert "async def update_task" in r1
        assert "async def delete_task" in r1

        manifest.write_text(MANIFEST_CREATE_GET)
        proc2 = run_cli(manifest, out)
        assert proc2.returncode == 0, proc2.stderr

        r2 = p["router"].read_text()
        assert "async def create_task" in r2
        assert "async def get_task" in r2
        # Removed from template → must disappear
        assert "async def list_tasks" not in r2
        assert "async def update_task" not in r2
        assert "async def delete_task" not in r2

        assert_valid_python(p["router"])

    def test_user_body_in_remaining_handler_preserved(self, tmp_path: Path) -> None:
        """Custom logic inside a route handler that stays must survive."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_FULL)
        run_cli(manifest, out)

        # User customizes the create_task handler body
        r = p["router"].read_text()
        r = r.replace(
            "    return await CreateTaskUseCase(repo).execute(data)",
            "    # custom: add audit log\n"
            '    print("creating task")\n'
            "    return await CreateTaskUseCase(repo).execute(data)",
        )
        p["router"].write_text(r)

        manifest.write_text(MANIFEST_CREATE_GET)
        run_cli(manifest, out)

        r2 = p["router"].read_text()
        assert "# custom: add audit log" in r2
        assert 'print("creating task")' in r2
        assert "async def delete_task" not in r2
        assert "async def update_task" not in r2

        assert_valid_python(p["router"])

    def test_user_added_helper_function_preserved(self, tmp_path: Path) -> None:
        """A user-defined top-level helper function (not in prev) must survive."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_FULL)
        run_cli(manifest, out)

        p["router"].write_text(
            p["router"].read_text()
            + dedent("""\


            def _require_admin(token: str) -> bool:
                \"\"\"User-written auth helper.\"\"\"
                return token == "secret"
            """)
        )

        manifest.write_text(MANIFEST_CREATE_GET)
        run_cli(manifest, out)

        r = p["router"].read_text()
        assert "_require_admin" in r
        assert "User-written auth helper" in r
        assert "async def delete_task" not in r

        assert_valid_python(p["router"])

    def test_state_file_tracks_route_handler_ids(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_FULL)
        run_cli(manifest, out)
        raw1 = json.loads(p["state"].read_text())
        ids1 = raw1["aggregates"]["task"]["presentation/router.py"]["top_level_ids"]
        assert "method:delete_task" in ids1

        manifest.write_text(MANIFEST_CREATE_GET)
        run_cli(manifest, out)
        raw2 = json.loads(p["state"].read_text())
        ids2 = raw2["aggregates"]["task"]["presentation/router.py"]["top_level_ids"]
        assert "method:delete_task" not in ids2
        assert "method:create_task" in ids2


# ─── infrastructure/repository.py ─────────────────────────────────────────────


class TestInfraRepositoryDeletion:
    def test_list_method_removed_when_use_case_dropped(self, tmp_path: Path) -> None:
        """The conditional `list()` method on the repo class is removed when
        the `list` use case is dropped from the manifest."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_WITH_LIST)
        proc1 = run_cli(manifest, out)
        assert proc1.returncode == 0, proc1.stderr

        infra1 = p["infra"].read_text()
        assert "async def list(self)" in infra1

        manifest.write_text(MANIFEST_WITHOUT_LIST)
        proc2 = run_cli(manifest, out)
        assert proc2.returncode == 0, proc2.stderr

        infra2 = p["infra"].read_text()
        assert "async def list(self)" not in infra2

        # Core methods always present
        assert "async def get_by_id(self" in infra2
        assert "async def save(self" in infra2

        assert_valid_python(p["infra"])

    def test_user_method_in_repo_class_preserved(self, tmp_path: Path) -> None:
        """A user-added method in the repo class that is not in prev is kept."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_WITH_LIST)
        run_cli(manifest, out)

        # User adds a custom method to the repository
        current = p["infra"].read_text()
        p["infra"].write_text(
            current.rstrip()
            + dedent("""\

                async def find_by_title(self, title: str):
                    \"\"\"User-written search method.\"\"\"
                    pass
            """)
        )

        manifest.write_text(MANIFEST_WITHOUT_LIST)
        run_cli(manifest, out)

        infra = p["infra"].read_text()
        assert "find_by_title" in infra
        assert "User-written search method" in infra
        assert "async def list(self)" not in infra

        assert_valid_python(p["infra"])

    def test_user_body_in_remaining_repo_method_preserved(self, tmp_path: Path) -> None:
        """Custom logic inside save() (which stays) must survive list() deletion."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_WITH_LIST)
        run_cli(manifest, out)

        # User customizes the save() body
        current = p["infra"].read_text()
        p["infra"].write_text(
            current.replace(
                "        await self._session.merge(self._to_model(entity))",
                "        # custom: flush after merge\n"
                "        await self._session.merge(self._to_model(entity))\n"
                "        await self._session.flush()",
            )
        )

        manifest.write_text(MANIFEST_WITHOUT_LIST)
        run_cli(manifest, out)

        infra = p["infra"].read_text()
        assert "# custom: flush after merge" in infra
        assert "await self._session.flush()" in infra
        assert "async def list(self)" not in infra

        assert_valid_python(p["infra"])

    def test_state_file_tracks_repo_method_ids(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_WITH_LIST)
        run_cli(manifest, out)
        raw1 = json.loads(p["state"].read_text())
        per_class1 = raw1["aggregates"]["task"]["infrastructure/repository.py"]["per_class_ids"]
        assert "method:list" in per_class1.get("class:SqlalchemyTaskRepository", [])

        manifest.write_text(MANIFEST_WITHOUT_LIST)
        run_cli(manifest, out)
        raw2 = json.loads(p["state"].read_text())
        per_class2 = raw2["aggregates"]["task"]["infrastructure/repository.py"]["per_class_ids"]
        assert "method:list" not in per_class2.get("class:SqlalchemyTaskRepository", [])
        assert "method:save" in per_class2.get("class:SqlalchemyTaskRepository", [])


# ─── Cross-file consistency ────────────────────────────────────────────────────


class TestCrossFileDeletion:
    def test_all_three_files_consistent_after_deletion(self, tmp_path: Path) -> None:
        """After removing use cases, all three soft-merge files are consistent:
        no orphan references to deleted classes."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        manifest.write_text(MANIFEST_FULL)
        run_cli(manifest, out)

        manifest.write_text(MANIFEST_CREATE_GET)
        proc = run_cli(manifest, out)
        assert proc.returncode == 0, proc.stderr

        # use_cases.py: only create + get
        uc = p["use_cases"].read_text()
        for cls in ("UpdateTaskUseCase", "DeleteTaskUseCase", "ListTasksUseCase"):
            assert cls not in uc, f"{cls} should have been deleted from use_cases.py"

        # router.py: only create + get handlers
        router = p["router"].read_text()
        for fn in ("update_task", "delete_task", "list_tasks"):
            assert fn not in router, f"{fn} should have been deleted from router.py"

        # All three soft-merge files are valid Python
        for key in ("use_cases", "router", "infra"):
            assert_valid_python(p[key])

    def test_multiple_delete_then_re_add_cycle(self, tmp_path: Path) -> None:
        """Remove a use case then add it back — the class must reappear cleanly."""
        manifest = tmp_path / "manifest.py"
        out = tmp_path / "out"
        p = paths(out)

        # Step 1: full
        manifest.write_text(MANIFEST_FULL)
        run_cli(manifest, out)
        assert "class DeleteTaskUseCase:" in p["use_cases"].read_text()

        # Step 2: remove delete
        manifest.write_text(MANIFEST_CREATE_GET)
        run_cli(manifest, out)
        assert "class DeleteTaskUseCase:" not in p["use_cases"].read_text()

        # Step 3: add delete back
        manifest.write_text(MANIFEST_FULL)
        proc = run_cli(manifest, out)
        assert proc.returncode == 0, proc.stderr
        assert "class DeleteTaskUseCase:" in p["use_cases"].read_text()

        assert_valid_python(p["use_cases"])

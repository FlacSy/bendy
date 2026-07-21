"""
Comprehensive tests for bendy.merger.

Covers edge cases in BlockParser, round-trip fidelity, and
CodeMerger behaviour across realistic DDD/FastAPI patterns.
"""

import textwrap

from bendy.merger import BlockParser, CodeBlock, CodeMerger, PrevGenerated, method_hashes, render

# ─── helpers ──────────────────────────────────────────────────────────────────


def dedent(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


def parse(text: str) -> list[CodeBlock]:
    return BlockParser().parse(dedent(text))


def prev_generated(prev_gen: str) -> PrevGenerated:
    """Build the PrevGenerated record (names + header/body hashes) that the real
    generator persists in state.json — i.e. what bendy generated last time."""
    tree = parse(prev_gen)
    return PrevGenerated(
        top_level={b.signature_id for b in tree if b.type in ("class", "method")},
        per_class={
            b.signature_id: {c.signature_id for c in b.child_blocks if c.type == "method"}
            for b in tree
            if b.type == "class"
        },
        top_level_hashes={b.signature_id: method_hashes(b) for b in tree if b.type == "method"},
        per_class_hashes={
            b.signature_id: {
                c.signature_id: method_hashes(c) for c in b.child_blocks if c.type == "method"
            }
            for b in tree
            if b.type == "class"
        },
    )


def do_merge(gen: str, user: str, prev_gen: str | None = None) -> str:
    p = BlockParser()
    m = CodeMerger()
    prev = prev_generated(prev_gen) if prev_gen is not None else None
    return render(m.merge(p.parse(dedent(gen)), p.parse(dedent(user)), prev=prev))


def ids_of(blocks: list[CodeBlock], type_: str) -> list[str]:
    return [b.signature_id for b in blocks if b.type == type_]


# ─── Parser: imports ──────────────────────────────────────────────────────────


class TestParserImports:
    def test_import_as_alias(self):
        blocks = parse("import numpy as np\n")
        assert ids_of(blocks, "import") == ["import:import numpy as np"]

    def test_from_import_as_alias(self):
        blocks = parse("from datetime import datetime as dt\n")
        assert ids_of(blocks, "import") == ["import:from datetime import datetime as dt"]

    def test_multiline_import_parens(self):
        """from x import (\n    y,\n    z,\n) should be ONE import block."""
        src = "from fastapi import (\n    APIRouter,\n    HTTPException,\n)\n"
        blocks = parse(src)
        imp_blocks = [b for b in blocks if b.type == "import"]
        assert len(imp_blocks) == 1
        assert imp_blocks[0].signature_id == "import:from fastapi import ("
        assert "    APIRouter," in imp_blocks[0].header_lines
        assert ")" in imp_blocks[0].header_lines

    def test_multiline_import_no_trailing_lines_left_as_raw(self):
        src = "from .dtos import (\n    OrderCreate,\n    OrderResponse,\n)\n\nclass X:\n    pass\n"
        blocks = parse(src)
        import_blocks = [b for b in blocks if b.type == "import"]
        class_blocks = [b for b in blocks if b.type == "class"]
        assert len(import_blocks) == 1
        assert len(class_blocks) == 1  # import did not steal 'class X' into its lines

    def test_several_imports_all_captured(self):
        src = dedent("""\
            import os
            import sys
            from pathlib import Path
            from typing import Optional, List
        """)
        blocks = parse(src)
        assert len([b for b in blocks if b.type == "import"]) == 4


# ─── Parser: classes ──────────────────────────────────────────────────────────


class TestParserClasses:
    def test_class_simple_base(self):
        src = dedent("""\
            class Repo(BaseRepo):
                pass
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        assert cls.signature_id == "class:Repo"
        assert "class Repo(BaseRepo):" in cls.header_lines

    def test_class_multiline_bases(self):
        src = dedent("""\
            class MyRepo(
                Generic[T],
                BaseRepository,
            ):
                def find(self) -> None:
                    pass
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        assert cls.signature_id == "class:MyRepo"
        assert cls.header_lines[0].strip().startswith("class MyRepo(")
        assert cls.header_lines[-1].strip() == "):"
        assert len(cls.header_lines) == 4

    def test_class_with_decorator(self):
        src = dedent("""\
            @dataclass
            class Config:
                timeout: int = 30
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        assert any("@dataclass" in ln for ln in cls.header_lines)
        assert cls.signature_id == "class:Config"

    def test_class_with_class_variables(self):
        src = dedent("""\
            class Config:
                timeout: int = 30
                retries: int = 3

                def run(self) -> None:
                    pass
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        raw_content = "\n".join(
            ln for b in cls.child_blocks if b.type == "raw_text" for ln in b.body_lines
        )
        assert "timeout" in raw_content
        assert "retries" in raw_content
        assert len([b for b in cls.child_blocks if b.type == "method"]) == 1

    def test_class_with_docstring(self):
        src = dedent("""\
            class OrderService:
                \"\"\"Handles order processing.\"\"\"

                def execute(self) -> None:
                    pass
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        raw_content = "\n".join(
            ln for b in cls.child_blocks if b.type == "raw_text" for ln in b.body_lines
        )
        assert "Handles order processing" in raw_content

    def test_multiple_classes_in_order(self):
        src = dedent("""\
            class A:
                pass

            class B:
                pass

            class C:
                pass
        """)
        class_ids = ids_of(parse(src), "class")
        assert class_ids == ["class:A", "class:B", "class:C"]


# ─── Parser: methods ──────────────────────────────────────────────────────────


class TestParserMethods:
    def test_dunder_methods_all_parsed(self):
        src = dedent("""\
            class MyClass:
                def __init__(self, x: int) -> None:
                    self.x = x

                def __repr__(self) -> str:
                    return f'MyClass({self.x})'

                def __eq__(self, other: object) -> bool:
                    return isinstance(other, MyClass) and self.x == other.x
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        method_ids = {b.signature_id for b in cls.child_blocks if b.type == "method"}
        assert method_ids == {"method:__init__", "method:__repr__", "method:__eq__"}

    def test_classmethod_decorator(self):
        src = dedent("""\
            class Repo:
                @classmethod
                def create(cls, data: dict) -> 'Repo':
                    return cls()
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        assert method.signature_id == "method:create"
        assert any("@classmethod" in ln for ln in method.header_lines)

    def test_staticmethod_decorator(self):
        src = dedent("""\
            class Utils:
                @staticmethod
                def validate(x: int) -> bool:
                    return x > 0
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        assert method.signature_id == "method:validate"
        assert any("@staticmethod" in ln for ln in method.header_lines)

    def test_multiple_decorators_all_in_header(self):
        src = dedent("""\
            class API:
                @router.get('/items')
                @requires_auth
                @cache(ttl=60)
                def list_items(self) -> list:
                    return []
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        decorator_lines = [ln for ln in method.header_lines if ln.lstrip().startswith("@")]
        assert len(decorator_lines) == 3
        assert method.signature_id == "method:list_items"

    def test_method_with_complex_return_annotation(self):
        src = dedent("""\
            class UC:
                async def list(
                    self,
                    filters: dict[str, str],
                ) -> list[Optional[OrderResponse]]:
                    return []
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        header_text = "\n".join(method.header_lines)
        assert "list[Optional[OrderResponse]]" in header_text
        assert method.signature_id == "method:list"

    def test_method_body_with_ellipsis(self):
        src = dedent("""\
            class AbstractRepo:
                def save(self, entity: object) -> None:
                    ...
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        assert "..." in "\n".join(method.body_lines)

    def test_nested_function_stays_in_body_not_parsed(self):
        """A def inside a method body goes to body_lines, never becomes a child block."""
        src = dedent("""\
            class UC:
                def execute(self) -> int:
                    def _inner(x: int) -> int:
                        return x * 2
                    result = _inner(21)
                    return result
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        body = "\n".join(method.body_lines)
        assert "def _inner(x: int) -> int:" in body
        assert "return x * 2" in body
        assert "result = _inner(21)" in body
        assert not method.child_blocks

    def test_body_with_multiline_dict_literal(self):
        src = dedent("""\
            class Builder:
                def build(self) -> dict:
                    return {
                        'key1': 'value1',
                        'key2': [1, 2, 3],
                        'key3': {
                            'nested': True,
                        },
                    }
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        body = "\n".join(method.body_lines)
        assert "'key1': 'value1'" in body
        assert "'nested': True" in body

    def test_body_with_nested_if_for_blocks(self):
        src = dedent("""\
            class UC:
                async def execute(self, items: list) -> list:
                    result = []
                    for item in items:
                        if item.is_valid():
                            processed = self._process(item)
                            result.append(processed)
                    return result
        """)
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        body = "\n".join(method.body_lines)
        assert "for item in items:" in body
        assert "if item.is_valid():" in body
        assert "result.append(processed)" in body


# ─── Parser: module-level content ─────────────────────────────────────────────


class TestParserModuleLevel:
    def test_empty_string(self):
        blocks = BlockParser().parse("")
        assert all(b.type == "raw_text" for b in blocks)

    def test_module_docstring_is_raw_text(self):
        src = '"""Module docstring."""\n\nimport os\n'
        blocks = BlockParser().parse(src)
        raw_blocks = [b for b in blocks if b.type == "raw_text"]
        raw_text = "\n".join(ln for b in raw_blocks for ln in b.body_lines)
        assert '"""Module docstring."""' in raw_text

    def test_top_level_raw_code(self):
        src = dedent("""\
            import os

            __all__ = ['MyClass']
            _SENTINEL = object()

            class MyClass:
                pass
        """)
        blocks = parse(src)
        raw_text = "\n".join(ln for b in blocks if b.type == "raw_text" for ln in b.body_lines)
        assert "__all__" in raw_text
        assert "_SENTINEL" in raw_text


# ─── Round-trip fidelity ──────────────────────────────────────────────────────


class TestRoundTrip:
    def _check(self, src: str) -> None:
        text = dedent(src)
        result = render(BlockParser().parse(text))
        assert result == text, f"\n--- EXPECTED ---\n{repr(text)}\n--- GOT ---\n{repr(result)}"

    def test_multiple_blank_lines_between_methods(self):
        self._check("""\
            class Svc:
                def foo(self) -> None:
                    pass


                def bar(self) -> None:
                    pass
        """)

    def test_class_with_docstring(self):
        self._check("""\
            class Svc:
                \"\"\"Service docstring.\"\"\"

                def run(self) -> None:
                    pass
        """)

    def test_module_with_all_block_types(self):
        self._check("""\
            \"\"\"Module docstring.\"\"\"

            import os
            from pathlib import Path

            __all__ = ['Foo']

            class Foo:
                x: int = 0

                @staticmethod
                def bar() -> str:
                    return 'baz'
        """)

    def test_multiline_class_inheritance(self):
        self._check("""\
            class MyRepo(
                Generic[T],
                BaseRepository,
            ):
                def find(self, id: int) -> None:
                    pass
        """)

    def test_multiline_import(self):
        self._check("""\
            from fastapi import (
                APIRouter,
                HTTPException,
                status,
            )
        """)

    def test_multiple_decorators(self):
        self._check("""\
            class API:
                @router.post('/orders')
                @requires_auth
                async def create_order(self, dto: CreateOrderDTO) -> OrderResponse:
                    return await self._uc.execute(dto)
        """)

    def test_nested_function_in_body(self):
        self._check("""\
            class UC:
                def execute(self) -> int:
                    def _inner(x: int) -> int:
                        return x * 2
                    return _inner(21)
        """)

    def test_real_use_case_module(self):
        """Round-trips a file matching src/order/application/use_cases.py structure."""
        self._check("""\
            from uuid import UUID
            from datetime import datetime, timezone
            from dataclasses import replace

            from ..domain.models import Order
            from ..domain.repository import OrderRepository
            from .dtos import (
                OrderCreate,
                OrderUpdate,
                OrderResponse,
            )


            class CreateOrderUseCase:
                def __init__(self, repository: OrderRepository) -> None:
                    self._repository = repository

                async def execute(self, data: OrderCreate) -> OrderResponse:
                    entity = Order(
                        customer_id=data.customer_id,
                        total_amount=data.total_amount,
                    )
                    await self._repository.save(entity)
                    return OrderResponse.model_validate(entity)


            class GetOrderUseCase:
                def __init__(self, repository: OrderRepository) -> None:
                    self._repository = repository

                async def execute(self, id: UUID) -> OrderResponse:
                    entity = await self._repository.get_by_id(id)
                    if entity is None:
                        raise ValueError(f'Order {id} not found')
                    return OrderResponse.model_validate(entity)
        """)


# ─── Merger: imports ──────────────────────────────────────────────────────────


class TestMergerImports:
    def test_user_imports_first_then_gen_additions(self):
        gen = "from fastapi import APIRouter\n"
        user = "import os\n"
        result = do_merge(gen, user)
        lines = [ln for ln in result.split("\n") if ln.strip()]
        assert lines[0].startswith("import os")
        assert lines[1].startswith("from fastapi import APIRouter")

    def test_exact_line_dedup(self):
        gen = "from typing import Optional\nfrom typing import List\n"
        user = "from typing import Optional\nfrom typing import Dict\n"
        result = do_merge(gen, user)
        assert result.count("from typing import Optional") == 1
        assert "from typing import List" in result
        assert "from typing import Dict" in result

    def test_multiline_import_preserved_in_user(self):
        gen = "from .dtos import OrderCreate\n"
        user = "from .dtos import (\n    OrderCreate,\n    OrderUpdate,\n)\n"
        result = do_merge(gen, user)
        # User's multi-line import is kept (user imports come first)
        assert "from .dtos import (" in result

    def test_all_user_imports_preserved(self):
        gen = "import os\n"
        user = "import os\nimport sys\nfrom pathlib import Path\n"
        result = do_merge(gen, user)
        assert "import sys" in result
        assert "from pathlib import Path" in result


# ─── Merger: classes ──────────────────────────────────────────────────────────


class TestMergerClasses:
    def test_gen_order_determines_class_order(self):
        gen = dedent("""\
            class A:
                def m(self) -> None:
                    pass

            class B:
                def m(self) -> None:
                    pass
        """)
        user = dedent("""\
            class B:
                def m(self) -> None:
                    do_b()

            class A:
                def m(self) -> None:
                    do_a()
        """)
        result = do_merge(gen, user)
        assert result.index("class A") < result.index("class B")

    def test_user_class_docstring_wins_over_gen(self):
        gen = dedent("""\
            class UC:
                \"\"\"Generated docstring.\"\"\"

                def execute(self) -> None:
                    pass
        """)
        user = dedent("""\
            class UC:
                \"\"\"User's improved docstring.\"\"\"

                def execute(self) -> None:
                    do_work()
        """)
        result = do_merge(gen, user)
        assert "User's improved docstring." in result
        assert "Generated docstring." not in result

    def test_gen_docstring_used_when_user_has_none(self):
        gen = dedent("""\
            class UC:
                \"\"\"Generated docstring.\"\"\"

                def execute(self) -> None:
                    pass
        """)
        user = dedent("""\
            class UC:
                def execute(self) -> None:
                    do_work()
        """)
        result = do_merge(gen, user)
        assert "Generated docstring." in result

    def test_class_bases_updated_from_gen(self):
        gen = dedent("""\
            class MyRepo(BaseRepository[Order]):
                def save(self, entity: Order) -> None:
                    pass
        """)
        user = dedent("""\
            class MyRepo:
                def save(self, entity: Order) -> None:
                    self._db.insert(entity.to_dict())
        """)
        result = do_merge(gen, user)
        assert "class MyRepo(BaseRepository[Order]):" in result
        assert "self._db.insert(entity.to_dict())" in result

    def test_multiple_classes_merged_independently(self):
        gen = dedent("""\
            class RepoA:
                def save(self) -> None:
                    pass

            class RepoB:
                def find(self) -> None:
                    pass
        """)
        user = dedent("""\
            class RepoA:
                def save(self) -> None:
                    db.insert(self)

            class RepoB:
                def find(self) -> None:
                    return db.select()
        """)
        result = do_merge(gen, user)
        assert "db.insert(self)" in result
        assert "db.select()" in result

    def test_merge_with_empty_user(self):
        gen = dedent("""\
            import os

            class NewClass:
                def run(self) -> None:
                    pass
        """)
        result = do_merge(gen, "")
        assert "import os" in result
        assert "class NewClass" in result

    def test_merge_with_empty_gen(self):
        user = dedent("""\
            from typing import Optional

            class MyClass:
                def method(self) -> None:
                    do_stuff()
        """)
        result = do_merge("", user)
        assert "from typing import Optional" in result
        assert "class MyClass" in result
        assert "do_stuff()" in result


# ─── Merger: methods ──────────────────────────────────────────────────────────


class TestMergerMethods:
    def test_method_order_follows_user(self):
        """Merged class keeps the USER's method order (so a hand-chosen layout,
        e.g. a finder deliberately placed before the generated `list()`, is
        preserved); shared methods are merged in place."""
        gen = dedent("""\
            class UC:
                def alpha(self) -> None:
                    pass

                def beta(self) -> None:
                    pass

                def gamma(self) -> None:
                    pass
        """)
        user = dedent("""\
            class UC:
                def gamma(self) -> None:
                    do_gamma()

                def alpha(self) -> None:
                    do_alpha()

                def beta(self) -> None:
                    do_beta()
        """)
        result = do_merge(gen, user)
        pos = {name: result.index(f"def {name}") for name in ("alpha", "beta", "gamma")}
        assert pos["gamma"] < pos["alpha"] < pos["beta"]  # the user's order
        # All user bodies preserved
        for name in ("alpha", "beta", "gamma"):
            assert f"do_{name}()" in result

    def test_user_only_method_appended_after_gen_methods(self):
        gen = dedent("""\
            class UC:
                def official(self) -> None:
                    pass
        """)
        user = dedent("""\
            class UC:
                def official(self) -> None:
                    do_official()

                def _helper(self) -> int:
                    return 42
        """)
        result = do_merge(gen, user)
        assert result.index("def official") < result.index("def _helper")
        assert "return 42" in result

    def test_init_header_updated_body_preserved(self):
        """Adding new constructor dependency while preserving user's setup code."""
        gen = dedent("""\
            class Svc:
                def __init__(
                    self,
                    repo: OrderRepository,
                    event_bus: EventBus,
                ) -> None:
                    pass
        """)
        user = dedent("""\
            class Svc:
                def __init__(self, repo: OrderRepository) -> None:
                    self._repo = repo
                    self._cache: dict = {}
        """)
        # user hasn't touched the __init__ signature (only its body), so the new
        # dependency propagates while the user's setup code is preserved.
        prev_gen = dedent("""\
            class Svc:
                def __init__(self, repo: OrderRepository) -> None:
                    pass
        """)
        result = do_merge(gen, user, prev_gen=prev_gen)
        assert "EventBus" in result  # new dependency in header
        assert "self._repo = repo" in result  # user's setup preserved
        assert "self._cache" in result

    def test_multiline_gen_header_replaces_user_single_line(self):
        gen = dedent("""\
            class UC:
                async def execute(
                    self,
                    dto: NewDTO,
                    ctx: RequestContext,
                ) -> NewResponse:
                    pass
        """)
        user = dedent("""\
            class UC:
                async def execute(self, dto: OldDTO) -> OldResponse:
                    result = self._service.run(dto)
                    return result
        """)
        # user only edited the body, not the signature, so the regenerated
        # (multi-line, new-typed) header replaces the user's old single-line one.
        prev_gen = dedent("""\
            class UC:
                async def execute(self, dto: OldDTO) -> OldResponse:
                    pass
        """)
        result = do_merge(gen, user, prev_gen=prev_gen)
        assert "NewDTO" in result
        assert "RequestContext" in result
        assert "NewResponse" in result
        assert "OldDTO" not in result
        assert "self._service.run(dto)" in result

    def test_user_pass_body_retained_not_overridden_by_gen_pass(self):
        """Even when both gen and user have 'pass', the user's body is taken."""
        gen = dedent("""\
            class UC:
                def execute(self) -> None:
                    pass
        """)
        user = dedent("""\
            class UC:
                def execute(self) -> None:
                    pass
        """)
        result = do_merge(gen, user)
        assert "def execute(self) -> None:" in result
        assert "pass" in result

    def test_new_gen_method_appended_after_user_methods(self):
        """A method that exists in gen but not the user is appended after the
        user's own methods (which keep their hand-chosen order)."""
        gen = dedent("""\
            class UC:
                def setup(self) -> None:
                    pass

                def new_method(self) -> str:
                    return 'new'

                def teardown(self) -> None:
                    pass
        """)
        user = dedent("""\
            class UC:
                def setup(self) -> None:
                    do_setup()

                def teardown(self) -> None:
                    do_teardown()
        """)
        result = do_merge(gen, user)
        assert "new_method" in result
        assert "do_setup()" in result
        assert "do_teardown()" in result
        assert (
            result.index("def setup")
            < result.index("def teardown")
            < result.index("def new_method")
        )

    def test_user_method_with_decorator_preserved(self):
        gen = dedent("""\
            class UC:
                def execute(self) -> None:
                    pass
        """)
        user = dedent("""\
            class UC:
                def execute(self) -> None:
                    do_work()

                @cached_property
                def config(self) -> Config:
                    return Config.load()
        """)
        result = do_merge(gen, user)
        assert "@cached_property" in result
        assert "Config.load()" in result

    def test_full_lifecycle_three_regen_steps(self):
        """
        Simulates three regeneration cycles on the same file.
        After each merge the user's code is preserved and only
        headers/new methods change.
        """
        gen_v1 = dedent("""\
            class OrderUC:
                def __init__(self, repo: OrderRepo) -> None:
                    pass

                async def create(self, dto: CreateDTO) -> OrderResponse:
                    pass
        """)
        gen_v2 = dedent("""\
            class OrderUC:
                def __init__(self, repo: OrderRepo, cache: Cache) -> None:
                    pass

                async def create(self, dto: CreateDTO) -> OrderResponse:
                    pass

                async def delete(self, id: UUID) -> None:
                    pass
        """)
        gen_v3 = dedent("""\
            class OrderUC:
                def __init__(self, repo: OrderRepo, cache: Cache, bus: EventBus) -> None:
                    pass

                async def create(self, dto: CreateDTO) -> OrderResponse:
                    pass

                async def delete(self, id: UUID) -> None:
                    pass
        """)

        # User edits file after v1
        user_after_v1 = dedent("""\
            class OrderUC:
                def __init__(self, repo: OrderRepo) -> None:
                    self._repo = repo
                    self._log = get_logger()

                async def create(self, dto: CreateDTO) -> OrderResponse:
                    order = Order.from_dto(dto)
                    await self._repo.save(order)
                    return OrderResponse.from_entity(order)
        """)

        p = BlockParser()
        m = CodeMerger()

        # Cycle 2: gen_v2 + user_after_v1 (prev = what we generated as v1). The
        # user never touched the __init__ signature, so `cache` propagates.
        merged_v2 = render(
            m.merge(p.parse(dedent(gen_v2)), p.parse(user_after_v1), prev=prev_generated(gen_v1))
        )

        assert "Cache" in merged_v2  # new dep in signature
        assert "self._repo = repo" in merged_v2  # user setup preserved
        assert "self._log = get_logger()" in merged_v2  # user setup preserved
        assert "Order.from_dto(dto)" in merged_v2  # user body preserved
        assert "async def delete" in merged_v2  # new method added

        # User edits the merged file: fills in delete's implementation
        user_after_v2 = dedent("""\
            class OrderUC:
                def __init__(self, repo: OrderRepo, cache: Cache) -> None:
                    self._repo = repo
                    self._log = get_logger()

                async def create(self, dto: CreateDTO) -> OrderResponse:
                    order = Order.from_dto(dto)
                    await self._repo.save(order)
                    return OrderResponse.from_entity(order)

                async def delete(self, id: UUID) -> None:
                    await self._repo.delete(id)
        """)

        # Cycle 3: gen_v3 + user's v2 file (prev = what we generated as v2).
        merged_v3 = render(
            m.merge(p.parse(dedent(gen_v3)), p.parse(user_after_v2), prev=prev_generated(gen_v2))
        )

        assert "EventBus" in merged_v3  # new dep in v3
        assert "self._repo = repo" in merged_v3  # still there
        assert "Order.from_dto(dto)" in merged_v3  # still there
        assert "self._repo.delete(id)" in merged_v3  # user's delete preserved

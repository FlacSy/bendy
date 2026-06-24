"""
Tests for bendy.merger — BlockParser, CodeMerger, render.

Each test is self-contained: it defines small Python source strings,
parses them, merges them, and checks the result.
"""

import textwrap

from bendy.merger import BlockParser, CodeBlock, CodeMerger, render

# ─── helpers ──────────────────────────────────────────────────────────────────


def dedent(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


def parse(text: str) -> list[CodeBlock]:
    return BlockParser().parse(dedent(text))


def merge(gen: str, user: str) -> str:
    parser = BlockParser()
    merger = CodeMerger()
    tree = merger.merge(parser.parse(dedent(gen)), parser.parse(dedent(user)))
    return render(tree)


# ─── BlockParser ──────────────────────────────────────────────────────────────


class TestBlockParser:
    def test_single_import(self):
        blocks = parse("import os\n")
        assert len(blocks) == 2  # import + trailing blank raw_text
        assert blocks[0].type == "import"
        assert blocks[0].signature_id == "import:import os"

    def test_from_import(self):
        blocks = parse("from pathlib import Path\n")
        assert blocks[0].type == "import"
        assert blocks[0].signature_id == "import:from pathlib import Path"

    def test_class_with_method(self):
        src = """\
            class Foo:
                def bar(self) -> None:
                    pass
        """
        blocks = parse(src)
        cls = next(b for b in blocks if b.type == "class")
        assert cls.signature_id == "class:Foo"
        assert cls.header_lines == ["class Foo:"]

        methods = [b for b in cls.child_blocks if b.type == "method"]
        assert len(methods) == 1
        assert methods[0].signature_id == "method:bar"
        assert "        pass" in methods[0].body_lines  # 8 spaces: class(4) + method body(4)

    def test_multiline_signature(self):
        src = """\
            def create(
                self,
                name: str,
                value: int,
            ) -> None:
                pass
        """
        blocks = parse(src)
        method = next(b for b in blocks if b.type == "method")
        assert method.signature_id == "method:create"
        assert len(method.header_lines) == 5  # def + 3 args + closing )
        assert method.header_lines[0].lstrip().startswith("def create(")
        assert method.header_lines[-1].lstrip().startswith(") -> None:")

    def test_decorator_attached_to_method(self):
        src = """\
            class Router:
                @property
                def path(self) -> str:
                    return '/foo'
        """
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        assert "@property" in method.header_lines[0]
        assert method.signature_id == "method:path"

    def test_body_preserves_blank_lines_and_comments(self):
        src = """\
            class Svc:
                def execute(self) -> None:
                    # step 1
                    x = 1

                    # step 2
                    return x
        """
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        body = "\n".join(method.body_lines)
        assert "# step 1" in body
        assert "# step 2" in body
        assert "" in method.body_lines  # blank line preserved

    def test_trailing_blank_not_swallowed_by_method(self):
        src = """\
            class Svc:
                def foo(self) -> None:
                    pass

                def bar(self) -> None:
                    pass
        """
        cls = next(b for b in parse(src) if b.type == "class")
        methods = [b for b in cls.child_blocks if b.type == "method"]
        assert len(methods) == 2
        # blank line between methods must NOT end up in foo's body
        foo = next(m for m in methods if m.signature_id == "method:foo")
        assert not any(ln == "" for ln in foo.body_lines[-1:] if foo.body_lines)

    def test_async_def(self):
        src = """\
            class UC:
                async def execute(self, dto: int) -> None:
                    await self._repo.save(dto)
        """
        cls = next(b for b in parse(src) if b.type == "class")
        method = next(b for b in cls.child_blocks if b.type == "method")
        assert method.signature_id == "method:execute"
        assert method.header_lines[0].lstrip().startswith("async def")


# ─── round-trip ───────────────────────────────────────────────────────────────


class TestRoundTrip:
    def _check(self, src: str) -> None:
        text = dedent(src)
        result = render(BlockParser().parse(text))
        assert result == text, f"\nEXPECTED:\n{text}\n\nGOT:\n{result}"

    def test_simple_module(self):
        self._check("""\
            import os
            from pathlib import Path

            class Foo:
                def bar(self) -> None:
                    pass
        """)

    def test_multiline_def_roundtrip(self):
        self._check("""\
            class UC:
                async def execute(
                    self,
                    name: str,
                    value: int,
                ) -> None:
                    result = name + str(value)
                    return result
        """)

    def test_decorator_roundtrip(self):
        self._check("""\
            class Router:
                @staticmethod
                def ping() -> str:
                    return 'pong'
        """)


# ─── CodeMerger ───────────────────────────────────────────────────────────────


class TestImportMerge:
    def test_no_duplicates(self):
        gen = "import os\nfrom pathlib import Path\n"
        user = "import os\nimport sys\n"
        result = merge(gen, user)
        assert result.count("import os") == 1
        assert "import sys" in result
        assert "from pathlib import Path" in result

    def test_user_imports_come_first(self):
        gen = "from fastapi import APIRouter\n"
        user = "import os\n"
        result = merge(gen, user)
        lines = [ln for ln in result.split("\n") if ln.strip()]
        assert lines[0].startswith("import os")
        assert lines[1].startswith("from fastapi")


class TestClassMerge:
    def test_new_class_from_gen_added(self):
        gen = dedent("""\
            class NewClass:
                def method(self) -> None:
                    pass
        """)
        user = ""
        result = render(CodeMerger().merge(BlockParser().parse(gen), BlockParser().parse(user)))
        assert "class NewClass" in result

    def test_user_class_header_updated_from_gen(self):
        gen = dedent("""\
            class Svc(BaseService):
                def run(self) -> None:
                    pass
        """)
        user = dedent("""\
            class Svc:
                def run(self) -> None:
                    # user logic
                    do_something()
        """)
        result = merge(gen, user)
        assert "class Svc(BaseService):" in result

    def test_user_only_class_preserved(self):
        gen = dedent("""\
            class GenClass:
                def method(self) -> None:
                    pass
        """)
        user = dedent("""\
            class GenClass:
                def method(self) -> None:
                    pass

            class UserOnlyClass:
                def custom(self) -> None:
                    return 42
        """)
        result = merge(gen, user)
        assert "class UserOnlyClass" in result
        assert "return 42" in result


class TestMethodMerge:
    def test_user_body_preserved_when_signature_updated(self):
        gen = dedent("""\
            class UC:
                async def execute(self, dto: NewDTO) -> NewResponse:
                    pass
        """)
        user = dedent("""\
            class UC:
                async def execute(self, dto: OldDTO) -> OldResponse:
                    # hand-written logic
                    result = self._repo.find(dto.id)
                    return result
        """)
        result = merge(gen, user)
        # signature updated
        assert "NewDTO" in result
        assert "NewResponse" in result
        assert "OldDTO" not in result
        # body preserved
        assert "# hand-written logic" in result
        assert "self._repo.find(dto.id)" in result

    def test_new_method_from_gen_added(self):
        gen = dedent("""\
            class UC:
                def old_method(self) -> None:
                    pass

                def new_method(self) -> str:
                    return 'new'
        """)
        user = dedent("""\
            class UC:
                def old_method(self) -> None:
                    # existing
                    do_old()
        """)
        result = merge(gen, user)
        assert "new_method" in result
        assert "do_old()" in result

    def test_user_only_method_preserved(self):
        gen = dedent("""\
            class UC:
                def execute(self) -> None:
                    pass
        """)
        user = dedent("""\
            class UC:
                def execute(self) -> None:
                    do_work()

                def _helper(self) -> int:
                    return 42
        """)
        result = merge(gen, user)
        assert "_helper" in result
        assert "return 42" in result

    def test_full_ddd_scenario(self):
        """Realistic use-case: gen updates DTO types, user keeps implementation."""
        gen = dedent("""\
            from uuid import UUID
            from .dtos import CreateOrderDTO, OrderResponse

            class CreateOrderUseCase:
                def __init__(self, repository: OrderRepository) -> None:
                    self._repository = repository

                async def execute(self, data: CreateOrderDTO) -> OrderResponse:
                    pass
        """)
        user = dedent("""\
            from uuid import UUID
            from .dtos import OldDTO, OldResponse
            from .events import OrderCreatedEvent

            class CreateOrderUseCase:
                def __init__(self, repository: OrderRepository) -> None:
                    self._repository = repository

                async def execute(self, data: OldDTO) -> OldResponse:
                    order = Order.create(data)
                    event = OrderCreatedEvent(order_id=order.id)
                    await self._repository.save(order)
                    await self._events.publish(event)
                    return OldResponse.from_entity(order)
        """)
        result = merge(gen, user)

        # Updated signature
        assert "CreateOrderDTO" in result
        assert "OrderResponse" in result

        # User's custom import preserved
        assert "from .events import OrderCreatedEvent" in result

        # User's implementation preserved
        assert "Order.create(data)" in result
        assert "OrderCreatedEvent" in result
        assert "self._events.publish(event)" in result

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PrevGenerated:
    top_level: set[str]
    per_class: dict[str, set[str]]


@dataclass
class CodeBlock:
    type: str
    signature_id: str
    header_lines: list[str]
    body_lines: list[str]
    indent_level: int
    child_blocks: list[CodeBlock] = field(default_factory=list)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _extract_name(lines: list[str]) -> str:
    for line in lines:
        s = line.lstrip()
        for prefix in ("async def ", "def ", "class "):
            if s.startswith(prefix):
                rest = s[len(prefix) :]
                name = ""
                for ch in rest:
                    if ch in ("(", ":", " "):
                        break
                    name += ch
                return name
    return ""


class BlockParser:
    def parse(self, text: str) -> list[CodeBlock]:
        blocks, _ = self._scan(text.split("\n"), 0, 0)
        return blocks

    def _scan(
        self,
        lines: list[str],
        start: int,
        level_indent: int,
    ) -> tuple[list[CodeBlock], int]:
        result: list[CodeBlock] = []
        raw_buf: list[str] = []
        decorators: list[str] = []
        raw_id = 0
        i = start

        def flush_raw() -> None:
            nonlocal raw_id
            if raw_buf:
                result.append(
                    CodeBlock(
                        type="raw_text",
                        signature_id=f"raw:{raw_id}",
                        header_lines=[],
                        body_lines=list(raw_buf),
                        indent_level=level_indent,
                    )
                )
                raw_id += 1
                raw_buf.clear()

        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()

            if not stripped:
                raw_buf.append(line)
                i += 1
                continue

            indent = _indent_of(line)

            if indent < level_indent:
                break

            if indent > level_indent:
                if decorators:
                    raw_buf.extend(decorators)
                    decorators = []
                raw_buf.append(line)
                i += 1
                continue

            if stripped.startswith("@"):
                flush_raw()
                decorators.append(line)
                i += 1
                continue

            if stripped.startswith("import ") or stripped.startswith("from "):
                flush_raw()
                imp_lines = decorators + [line]
                balance = line.count("(") - line.count(")")
                i += 1
                while balance > 0 and i < len(lines):
                    imp_lines.append(lines[i])
                    balance += lines[i].count("(") - lines[i].count(")")
                    i += 1
                result.append(
                    CodeBlock(
                        type="import",
                        signature_id=f"import:{stripped.rstrip()}",
                        header_lines=imp_lines,
                        body_lines=[],
                        indent_level=indent,
                    )
                )
                decorators = []
                continue

            if stripped.startswith("class "):
                flush_raw()
                block, i = self._parse_class(lines, i, indent, decorators)
                decorators = []
                result.append(block)
                continue

            if stripped.startswith("def ") or stripped.startswith("async def "):
                flush_raw()
                block, i = self._parse_method(lines, i, indent, decorators)
                decorators = []
                result.append(block)
                continue

            if decorators:
                raw_buf.extend(decorators)
                decorators = []
            raw_buf.append(line)
            i += 1

        flush_raw()
        return result, i

    def _collect_header(self, lines: list[str], start: int) -> tuple[list[str], int]:
        header: list[str] = []
        balance = 0
        i = start

        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()
            for ch in line:
                if ch == "(":
                    balance += 1
                elif ch == ")":
                    balance -= 1
            header.append(line)
            i += 1
            # strip inline comment before checking the trailing colon
            code_part = stripped.split("#")[0].rstrip()
            if balance <= 0 and code_part.endswith(":"):
                break

        return header, i

    def _parse_class(
        self,
        lines: list[str],
        start: int,
        indent: int,
        decorators: list[str],
    ) -> tuple[CodeBlock, int]:
        sig_lines, i = self._collect_header(lines, start)
        name = _extract_name(sig_lines)
        child_blocks, i = self._scan(lines, i, indent + 4)
        return CodeBlock(
            type="class",
            signature_id=f"class:{name}",
            header_lines=decorators + sig_lines,
            body_lines=[],
            indent_level=indent,
            child_blocks=child_blocks,
        ), i

    def _parse_method(
        self,
        lines: list[str],
        start: int,
        indent: int,
        decorators: list[str],
    ) -> tuple[CodeBlock, int]:
        sig_lines, i = self._collect_header(lines, start)
        name = _extract_name(sig_lines)
        body_indent = indent + 4
        body: list[str] = []
        trailing: list[str] = []

        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()
            if not stripped:
                trailing.append(line)
                i += 1
                continue
            if _indent_of(line) < body_indent:
                break
            body.extend(trailing)
            trailing.clear()
            body.append(line)
            i += 1

        # trailing blanks are inter-block spacing — give them back to the parent
        i -= len(trailing)

        return CodeBlock(
            type="method",
            signature_id=f"method:{name}",
            header_lines=decorators + sig_lines,
            body_lines=body,
            indent_level=indent,
        ), i


class CodeMerger:
    def merge(
        self,
        gen_tree: list[CodeBlock],
        user_tree: list[CodeBlock],
        prev: PrevGenerated | None = None,
    ) -> list[CodeBlock]:
        result: list[CodeBlock] = []

        gen_imports = [b for b in gen_tree if b.type == "import"]
        user_imports = [b for b in user_tree if b.type == "import"]
        result.extend(self._merge_imports(gen_imports, user_imports))

        gen_classes: dict[str, CodeBlock] = {
            b.signature_id: b for b in gen_tree if b.type == "class"
        }
        gen_top_methods: dict[str, CodeBlock] = {
            b.signature_id: b for b in gen_tree if b.type == "method"
        }
        user_classes: dict[str, CodeBlock] = {
            b.signature_id: b for b in user_tree if b.type == "class"
        }
        user_top_methods: dict[str, CodeBlock] = {
            b.signature_id: b for b in user_tree if b.type == "method"
        }

        stale: set[str] = set()
        if prev:
            stale = prev.top_level - gen_classes.keys() - gen_top_methods.keys()

        for block in gen_tree:
            if block.type == "import":
                continue
            if block.type == "class":
                if block.signature_id in user_classes:
                    result.append(
                        self._merge_class(
                            block,
                            user_classes[block.signature_id],
                            prev_method_ids=(
                                prev.per_class.get(block.signature_id) if prev else None
                            ),
                        )
                    )
                else:
                    result.append(block)
            elif block.type == "method":
                if block.signature_id in user_top_methods:
                    user_m = user_top_methods[block.signature_id]
                    result.append(
                        CodeBlock(
                            type="method",
                            signature_id=block.signature_id,
                            header_lines=block.header_lines,
                            body_lines=user_m.body_lines,
                            indent_level=block.indent_level,
                        )
                    )
                else:
                    result.append(block)
            else:
                result.append(block)

        for block in user_tree:
            if block.type == "class" and block.signature_id not in gen_classes:
                if block.signature_id not in stale:
                    result.append(block)
            elif block.type == "method" and block.signature_id not in gen_top_methods:
                if block.signature_id not in stale:
                    result.append(block)

        return result

    def _merge_imports(
        self,
        gen_imports: list[CodeBlock],
        user_imports: list[CodeBlock],
    ) -> list[CodeBlock]:
        seen: set[str] = set()
        result: list[CodeBlock] = []
        for block in user_imports:
            if block.signature_id not in seen:
                seen.add(block.signature_id)
                result.append(block)
        for block in gen_imports:
            if block.signature_id not in seen:
                seen.add(block.signature_id)
                result.append(block)
        return result

    def _merge_class(
        self,
        gen_cls: CodeBlock,
        user_cls: CodeBlock,
        prev_method_ids: set[str] | None = None,
    ) -> CodeBlock:
        return CodeBlock(
            type="class",
            signature_id=gen_cls.signature_id,
            header_lines=gen_cls.header_lines,
            body_lines=[],
            indent_level=gen_cls.indent_level,
            child_blocks=self._merge_children(
                gen_cls.child_blocks,
                user_cls.child_blocks,
                prev_method_ids=prev_method_ids,
            ),
        )

    def _merge_children(
        self,
        gen_children: list[CodeBlock],
        user_children: list[CodeBlock],
        prev_method_ids: set[str] | None = None,
    ) -> list[CodeBlock]:
        result: list[CodeBlock] = []

        gen_methods: dict[str, CodeBlock] = {
            b.signature_id: b for b in gen_children if b.type == "method"
        }
        user_methods: dict[str, CodeBlock] = {
            b.signature_id: b for b in user_children if b.type == "method"
        }

        user_raw = [b for b in user_children if b.type != "method"]
        gen_raw = [b for b in gen_children if b.type != "method"]
        # a raw_text block of only blank lines doesn't count as user content
        user_has_content = any(any(line.strip() for line in b.body_lines) for b in user_raw)
        result.extend(user_raw if user_has_content else gen_raw)

        stale_methods: set[str] = (prev_method_ids or set()) - gen_methods.keys()

        for block in gen_children:
            if block.type != "method":
                continue
            if block.signature_id in user_methods:
                user_m = user_methods[block.signature_id]
                result.append(
                    CodeBlock(
                        type="method",
                        signature_id=block.signature_id,
                        header_lines=block.header_lines,
                        body_lines=user_m.body_lines,
                        indent_level=block.indent_level,
                    )
                )
            else:
                result.append(block)

        for block in user_children:
            if block.type == "method" and block.signature_id not in gen_methods:
                if block.signature_id not in stale_methods:
                    result.append(block)

        return result


def render(tree: list[CodeBlock]) -> str:
    parts: list[str] = []

    def emit(block: CodeBlock) -> None:
        parts.extend(block.header_lines)
        if block.child_blocks:
            for child in block.child_blocks:
                emit(child)
        else:
            parts.extend(block.body_lines)

    for block in tree:
        emit(block)

    return "\n".join(parts)

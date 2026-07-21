from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class PrevGenerated:
    top_level: set[str]
    per_class: dict[str, set[str]]
    # content hashes of what was generated last time, per signature_id — used for
    # a 3-way merge: a method the user hasn't touched (its current hash still
    # matches what we generated) is regenerated so template/field changes reach
    # it; a method the user has edited is preserved verbatim (decorators,
    # signature and body), so hand-added auth gates / params / logic survive.
    top_level_hashes: dict[str, dict[str, str]] = field(default_factory=dict)
    per_class_hashes: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    # whole-class content hash (class_id -> sha256), for the class-level 3-way.
    top_level_class_hashes: dict[str, str] = field(default_factory=dict)


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


def _hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def header_hash(block: CodeBlock) -> str:
    """Hash of a method's header — decorators + signature."""
    return _hash(block.header_lines)


def body_hash(block: CodeBlock) -> str:
    """Hash of a method's body."""
    return _hash(block.body_lines)


def method_hashes(block: CodeBlock) -> dict[str, str]:
    """Header+body hashes recorded for a generated method, for the next merge's
    3-way. Header and body are tracked separately so a user's edit to one part
    (e.g. an added auth-gate decorator) is preserved while the other part still
    picks up template/field changes."""
    return {"header": header_hash(block), "body": body_hash(block)}


def class_hash(block: CodeBlock) -> str:
    """Hash of a whole class (header + every field/method), for a class-level
    3-way. A class the user hasn't touched can be regenerated wholesale so
    manifest field changes reach it (relevant for field-only classes like DTOs
    and dataclasses, which have no per-method merge to hang edits on); a class
    they've edited falls back to the per-method merge that preserves edits."""
    return hashlib.sha256(render([block]).encode()).hexdigest()


def _parse_import(block: CodeBlock) -> tuple[str | None, frozenset[str]]:
    """`from MODULE import a, b as c` -> ("MODULE", {"a", "c"}); a plain
    `import X [as Y]` (or anything unparseable) -> (None, {the statement}).
    Used to decide whether a generated import is already satisfied by the user's
    imports (same module, its names a superset), so an edited import line — e.g.
    `from pydantic import ...` with an extra name the user added — isn't
    duplicated, while a genuinely new import still gets added."""
    text = " ".join(line.strip() for line in block.header_lines).replace("(", " ").replace(")", " ")
    if text.startswith("from "):
        module, sep, names_part = text[5:].partition(" import ")
        if sep:
            names = frozenset(
                n.strip().split(" as ")[-1].strip() for n in names_part.split(",") if n.strip()
            )
            return module.strip(), names
    return None, frozenset({text.strip()})


def _import_satisfied_by(
    gen_block: CodeBlock, user_parsed: list[tuple[str | None, frozenset[str]]]
) -> bool:
    gen_module, gen_names = _parse_import(gen_block)
    for user_module, user_names in user_parsed:
        if gen_module is None:
            if user_module is None and gen_names == user_names:
                return True
        elif user_module == gen_module and gen_names <= user_names:
            return True
    return False


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
                # a decorator may span multiple lines (e.g. FastAPI's
                # `@router.get(\n    ...,\n    dependencies=[...],\n)`); consume
                # the whole call so the entire decorator stays attached to the
                # method it decorates (part of its header_lines) rather than
                # leaking into a raw_text block — otherwise a merge can't tell
                # which method a hand-edited decorator belongs to.
                decorators.append(line)
                balance = line.count("(") - line.count(")")
                i += 1
                while balance > 0 and i < len(lines):
                    decorators.append(lines[i])
                    balance += lines[i].count("(") - lines[i].count(")")
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

        # Imports are kept in their user-file positions (so blank lines between
        # import groups survive); newly-generated imports the user doesn't have
        # are injected right after the last existing import (see below).
        user_parsed_imports = [_parse_import(b) for b in user_tree if b.type == "import"]
        gen_only_imports = [
            b
            for b in gen_tree
            if b.type == "import" and not _import_satisfied_by(b, user_parsed_imports)
        ]

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

        # Walk the USER file in order, so its layout — the position of each
        # class/function AND the blank lines and comments between them — is
        # preserved; shared blocks are merged in place, user-only blocks kept
        # (unless stale), raw blocks (spacing, hand comments) passed through.
        emitted: set[str] = set()
        last_import_pos = 0
        for block in user_tree:
            if block.type == "import":
                result.append(block)
                last_import_pos = len(result)
                continue
            if block.type == "class":
                sid = block.signature_id
                if sid in gen_classes:
                    prev_cls_hash = prev.top_level_class_hashes.get(sid) if prev else None
                    if prev_cls_hash is not None and class_hash(block) == prev_cls_hash:
                        # untouched since last generation -> regenerate wholesale,
                        # so manifest field/method changes reach it (matters for
                        # field-only classes like DTOs/dataclasses).
                        result.append(gen_classes[sid])
                    else:
                        result.append(
                            self._merge_class(
                                gen_classes[sid],
                                block,
                                prev_method_ids=(prev.per_class.get(sid) if prev else None),
                                prev_method_hashes=(
                                    prev.per_class_hashes.get(sid) if prev else None
                                ),
                            )
                        )
                    emitted.add(sid)
                elif sid not in stale:
                    result.append(block)  # user-only class, kept in position
                    emitted.add(sid)
            elif block.type == "method":
                sid = block.signature_id
                if sid in gen_top_methods:
                    result.append(
                        self._pick_method(
                            gen_top_methods[sid],
                            block,
                            prev.top_level_hashes.get(sid) if prev else None,
                        )
                    )
                    emitted.add(sid)
                elif sid not in stale:
                    result.append(block)  # user-only function, kept in position
                    emitted.add(sid)
            else:
                result.append(block)  # raw: blank lines / hand comments, in place

        # Inject newly-generated imports right after the user's last import
        # (keeps them in the import block, ahead of any code).
        for offset, imp in enumerate(gen_only_imports):
            result.insert(last_import_pos + offset, imp)

        # Append newly-generated top-level classes/functions the user doesn't
        # have yet, in generation order.
        for block in gen_tree:
            if block.type in ("class", "method") and block.signature_id not in emitted:
                if (
                    block.signature_id not in user_classes
                    and block.signature_id not in user_top_methods
                ):
                    result.append(block)

        return result

    def _pick_method(
        self,
        gen_block: CodeBlock,
        user_block: CodeBlock,
        prev: dict[str, str] | None,
    ) -> CodeBlock:
        """3-way merge of a method present in both trees, at header/body
        granularity. For each part: if the user's current content still matches
        what we generated last time (`prev`), they haven't touched it → take the
        fresh gen part so template/field changes propagate; otherwise keep the
        user's part, so hand-edited decorators (auth gates, `response_model`),
        signatures (extra params) and bodies survive. With no recorded prev hash
        (first regen after upgrading to hash-tracking, or brand-new tracking)
        the user's part is preserved — never silently clobber an edit."""
        prev = prev or {}
        prev_header = prev.get("header")
        prev_body = prev.get("body")

        header = (
            gen_block.header_lines
            if prev_header is not None and header_hash(user_block) == prev_header
            else user_block.header_lines
        )
        body = (
            gen_block.body_lines
            if prev_body is not None and body_hash(user_block) == prev_body
            else user_block.body_lines
        )
        return CodeBlock(
            type="method",
            signature_id=gen_block.signature_id,
            header_lines=header,
            body_lines=body,
            indent_level=gen_block.indent_level,
        )

    def _merge_class(
        self,
        gen_cls: CodeBlock,
        user_cls: CodeBlock,
        prev_method_ids: set[str] | None = None,
        prev_method_hashes: dict[str, str] | None = None,
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
                prev_method_hashes=prev_method_hashes,
            ),
        )

    def _merge_children(
        self,
        gen_children: list[CodeBlock],
        user_children: list[CodeBlock],
        prev_method_ids: set[str] | None = None,
        prev_method_hashes: dict[str, str] | None = None,
    ) -> list[CodeBlock]:
        result: list[CodeBlock] = []

        gen_methods: dict[str, CodeBlock] = {
            b.signature_id: b for b in gen_children if b.type == "method"
        }
        user_methods: dict[str, CodeBlock] = {
            b.signature_id: b for b in user_children if b.type == "method"
        }

        stale_methods: set[str] = (prev_method_ids or set()) - gen_methods.keys()
        prev_hashes = prev_method_hashes or {}
        emitted: set[str] = set()

        # Walk the user's class body in order: methods are 3-way merged in place
        # (a hand-placed method keeps its position, e.g. a finder before the
        # generated `list()`), user-only methods are kept, and raw blocks (blank
        # lines, hand comments) pass through so the layout is preserved.
        for block in user_children:
            if block.type == "method":
                sid = block.signature_id
                if sid in gen_methods:
                    result.append(self._pick_method(gen_methods[sid], block, prev_hashes.get(sid)))
                    emitted.add(sid)
                elif sid not in stale_methods:
                    result.append(block)
                    emitted.add(sid)
            else:
                result.append(block)  # raw: blank lines / hand comments, in place

        # Then append newly-generated methods the user doesn't have yet, in
        # generation order.
        for block in gen_children:
            if block.type != "method":
                continue
            if block.signature_id not in emitted and block.signature_id not in user_methods:
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

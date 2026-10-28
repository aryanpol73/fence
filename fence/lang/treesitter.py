"""tree-sitter plugins for JavaScript, TypeScript/TSX, Go and Java.

tree-sitter recovers from syntax errors instead of failing, so these plugins
always return symbols; a file with errors simply yields fewer of them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property

from fence.lang import Symbol, normalize_string_literal

MAX_SOURCE_BYTES = 2_000_000


@dataclass
class TreeSitterPlugin:
    name: str
    extensions: tuple[str, ...]
    language_factory: Callable[[], object]
    scopes: dict[str, str]  # node type -> "function" | "class"
    string_types: frozenset[str] = frozenset()
    comment_types: frozenset[str] = frozenset({"comment", "line_comment", "block_comment"})

    @cached_property
    def parser(self):
        import tree_sitter as ts

        return ts.Parser(ts.Language(self.language_factory()))

    def _tree(self, source: str):
        data = source.encode("utf-8", errors="replace")
        if len(data) > MAX_SOURCE_BYTES:
            return None
        return self.parser.parse(data)

    def symbols(self, source: str) -> list[Symbol] | None:
        tree = self._tree(source)
        if tree is None:
            return None
        out: list[Symbol] = []
        stack = [(tree.root_node, ())]
        # Iterative pre-order walk: parents before children, no recursion limit.
        while stack:
            node, prefix = stack.pop()
            names = prefix
            kind = self.scopes.get(node.type)
            if kind:
                name = self._name(node)
                if name:
                    names = (*prefix, name)
                    start = node.start_point[0] + 1
                    out.append(Symbol(".".join(names), kind, start, node.end_point[0] + 1))
            for child in reversed(node.children):
                stack.append((child, names))
        return out

    def _name(self, node) -> str | None:
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is None or value.type not in ("arrow_function", "function_expression",
                                                   "function"):
                return None
        if node.type == "method_declaration" and self.name == "go":
            recv = node.child_by_field_name("receiver")
            method = node.child_by_field_name("name")
            rtype = _first_of_type(recv, "type_identifier") if recv is not None else None
            if method is None:
                return None
            m = method.text.decode("utf-8", "replace")
            return f"{rtype.text.decode('utf-8', 'replace')}.{m}" if rtype is not None else m
        n = node.child_by_field_name("name")
        return n.text.decode("utf-8", "replace") if n is not None else None

    def tokens(self, source: str) -> list[str]:
        tree = self._tree(source)
        if tree is None:
            from fence.lang import fallback_tokens

            return fallback_tokens(source)
        out: list[str] = []
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type in self.comment_types:
                continue
            if node.type in self.string_types:
                out.append(normalize_string_literal(node.text.decode("utf-8", "replace")))
                continue
            if node.child_count == 0:
                text = node.text.decode("utf-8", "replace")
                if text.strip() and text not in (";",):
                    out.append(text)
                continue
            stack.extend(reversed(node.children))
        # Trailing commas and semicolons are layout that formatters add and remove.
        return [t for i, t in enumerate(out) if not (t == "," and i + 1 < len(out)
                                                    and out[i + 1] in (")", "]", "}"))]


def _first_of_type(node, type_name: str):
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == type_name:
            return n
        stack.extend(reversed(n.children))
    return None


_JS_SCOPES = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "method_definition": "function",
    "class_declaration": "class",
    "variable_declarator": "function",
}
_JS_STRINGS = frozenset({"string", "template_string"})


def _ts(attr: str) -> Callable[[], object]:
    def load():
        import tree_sitter_typescript

        return getattr(tree_sitter_typescript, attr)()

    return load


def _mod(module: str) -> Callable[[], object]:
    def load():
        import importlib

        return importlib.import_module(module).language()

    return load


def plugins() -> list[TreeSitterPlugin]:
    ts_scopes = {
        **_JS_SCOPES,
        "interface_declaration": "class",
        "abstract_class_declaration": "class",
        "enum_declaration": "class",
        "function_signature": "function",
    }
    return [
        TreeSitterPlugin("javascript", (".js", ".jsx", ".mjs", ".cjs"),
                         _mod("tree_sitter_javascript"), _JS_SCOPES, _JS_STRINGS),
        TreeSitterPlugin("typescript", (".ts", ".mts", ".cts"), _ts("language_typescript"),
                         ts_scopes, _JS_STRINGS),
        TreeSitterPlugin("tsx", (".tsx",), _ts("language_tsx"), ts_scopes, _JS_STRINGS),
        TreeSitterPlugin("go", (".go",), _mod("tree_sitter_go"),
                         {"function_declaration": "function", "method_declaration": "function",
                          "type_spec": "class"},
                         frozenset({"interpreted_string_literal", "raw_string_literal"})),
        TreeSitterPlugin("java", (".java",), _mod("tree_sitter_java"),
                         {"class_declaration": "class", "interface_declaration": "class",
                          "enum_declaration": "class", "record_declaration": "class",
                          "method_declaration": "function",
                          "constructor_declaration": "function"},
                         frozenset({"string_literal", "text_block"})),
    ]

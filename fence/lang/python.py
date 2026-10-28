"""Python plugin: the stdlib `ast` and `tokenize` modules, which implement the exact
grammar of the running interpreter. No third-party parser is needed."""

from __future__ import annotations

import ast
import io
import tokenize

from fence.lang import Symbol, normalize_string_literal

_Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
_SKIP = {
    tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
    tokenize.ENCODING, tokenize.ENDMARKER,
}


class PythonPlugin:
    name = "python"
    extensions = (".py", ".pyi")

    def symbols(self, source: str) -> list[Symbol] | None:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return None
        out: list[Symbol] = []
        for node, names in _walk_scopes(tree, ()):
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            out.append(Symbol(".".join(names), kind, start, node.end_lineno or node.lineno))
        return out

    def tokens(self, source: str) -> list[str]:
        out: list[str] = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(source).readline):
                if tok.type in _SKIP:
                    continue
                if tok.type == tokenize.STRING:
                    out.append(normalize_string_literal(tok.string))
                else:
                    out.append(tok.string)
        except (tokenize.TokenError, SyntaxError, IndentationError):
            from fence.lang import fallback_tokens

            return fallback_tokens(source)
        # A trailing comma is layout in Python (black adds and removes them).
        return [t for i, t in enumerate(out) if not (t == "," and i + 1 < len(out)
                                                    and out[i + 1] in (")", "]", "}"))]


def _walk_scopes(node: ast.AST, prefix: tuple[str, ...]):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _Scope):
            names = (*prefix, child.name)
            yield child, names
            yield from _walk_scopes(child, names)
        else:
            # Scopes can sit inside if/try/with blocks; keep looking with the same prefix.
            yield from _walk_scopes(child, prefix)

"""Pluggable language layer: symbols and normalized tokens per language.

A plugin answers two questions about source text: which named scopes
(functions, methods, classes) exist and where, and what the code is once
whitespace, comments and quote style are stripped away. The first gives symbol
anchors; the second gives fingerprints for move detection and format-only
commit detection. Unsupported languages fall back to file and line anchors,
and `fallback_tokens` gives a whitespace-insensitive token stream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, Protocol

SymbolKind = Literal["function", "class", "module"]
MODULE = "<module>"


@dataclass(frozen=True)
class Symbol:
    qualname: str
    kind: SymbolKind
    start_line: int  # 1-based, first decorator/annotation line if any
    end_line: int


class LanguagePlugin(Protocol):
    name: str
    extensions: tuple[str, ...]

    def symbols(self, source: str) -> list[Symbol] | None:
        """All named scopes, parents before children. None if the source cannot be parsed."""

    def tokens(self, source: str) -> list[str]:
        """Code tokens with comments and layout removed and string quoting normalized."""


_REGISTRY: dict[str, LanguagePlugin] = {}
_BY_EXT: dict[str, str] = {}


def register(plugin: LanguagePlugin) -> None:
    _REGISTRY[plugin.name] = plugin
    for ext in plugin.extensions:
        _BY_EXT[ext] = plugin.name


def _ensure_loaded() -> None:
    if _REGISTRY:
        return
    from fence.lang import python, treesitter

    register(python.PythonPlugin())
    for plugin in treesitter.plugins():
        register(plugin)


def plugin_for(path: str, enabled: list[str] | None = None) -> LanguagePlugin | None:
    _ensure_loaded()
    name = _BY_EXT.get(PurePosixPath(path).suffix.lower())
    if name is None or (enabled is not None and _family(name) not in enabled):
        return None
    return _REGISTRY[name]


def _family(name: str) -> str:
    return "typescript" if name == "tsx" else name


def supported_languages() -> list[str]:
    _ensure_loaded()
    return sorted({_family(n) for n in _REGISTRY})


def enclosing_symbol(path: str, source: str, line: int) -> Symbol | None:
    """Innermost symbol containing `line`; `<module>` outside any scope; None if unparsable
    or unsupported."""
    if line < 1:
        raise ValueError(f"line must be >= 1, got {line}")
    total = len(source.splitlines())
    if line > total:
        return None
    plugin = plugin_for(path)
    if plugin is None:
        return None
    syms = plugin.symbols(source)
    if syms is None:
        return None
    best: Symbol | None = None
    for s in syms:  # parents come before children, so the last match is the innermost
        if s.start_line <= line <= s.end_line:
            best = s
    return best or Symbol(MODULE, "module", 1, total)


_FALLBACK_TOKEN = re.compile(r"\w+|[^\w\s]")


def fallback_tokens(source: str) -> list[str]:
    return _FALLBACK_TOKEN.findall(source)


def tokens_for(path: str, source: str) -> list[str]:
    plugin = plugin_for(path)
    return plugin.tokens(source) if plugin else fallback_tokens(source)


def normalize_string_literal(text: str) -> str:
    """'x', "x", '''x''', r"x", `x` -> a single canonical token, so quote style is ignored."""
    body = text.lstrip("rRbBuUfF")
    for q in ('"""', "'''", '"', "'", "`"):
        if body.startswith(q) and body.endswith(q) and len(body) >= 2 * len(q):
            body = body[len(q) : -len(q)]
            break
    return "STR:" + body

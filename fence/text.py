"""Make untrusted repository text safe to print in a terminal or PR comment."""

from __future__ import annotations

import unicodedata

# Bidi controls can make code or commit text display differently from what it is
# ("Trojan Source"). Zero-width characters can hide text.
_INVISIBLE = {
    *map(chr, range(0x202A, 0x202F)),
    *map(chr, range(0x2066, 0x206A)),
    "​",
    "‌",
    "‍",
    "‎",
    "‏",
    "﻿",
}


def clean(text: str, *, max_len: int = 500, keep_newlines: bool = False) -> str:
    """Drop control and invisible characters, keep tabs as spaces, cap the length.

    Escape sequences such as "\\x1b[2J" would otherwise be executed by the terminal.
    """
    out = []
    for ch in text:
        if ch == "\n" and keep_newlines:
            out.append(ch)
        elif ch == "\t":
            out.append("    ")
        elif ch in _INVISIBLE or unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co"):
            out.append("�" if ch == "\x1b" else "")
        else:
            out.append(ch)
    result = "".join(out)
    return result if len(result) <= max_len else result[: max_len - 1] + "…"


def quoted(text: str) -> str:
    """Wrap a verbatim quote in delimiters it does not contain, so a quote that starts
    with "fence" does not print as ""fence" ...". Double quotes unless the text has one."""
    return f"'{text}'" if '"' in text else f'"{text}"'


def is_bot(name: str | None) -> bool:
    """GitHub app accounts (dependabot[bot], renovate[bot], github-actions[bot]) carry a
    "[bot]" suffix that human logins cannot have. Their text is generated, often pasted
    from upstream release notes, so it never speaks for this repository."""
    return bool(name) and name.strip().lower().endswith("[bot]")

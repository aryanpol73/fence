"""Find an explicitly stated reason in untrusted text, and quote it verbatim.

A "stated reason" is a sentence that explains causation ("because", "otherwise",
"produced duplicate charges"). Fence never paraphrases it: it returns the exact
sentence (cleaned of control characters) or nothing. Sentences that try to talk
to a tool ("ignore previous instructions") are dropped, never quoted.
"""

from __future__ import annotations

import re

from fence.text import clean

MAX_QUOTE = 300

_REASON = re.compile(
    r"\b(because|since|so that|otherwise|or else|to (prevent|avoid|stop|ensure|guard|protect)|"
    r"prevents?|prevented|caus(e|es|ed|ing)|produc(es|ed|ing)|led to|leads to|"
    r"result(s|ed)? in|in order to|due to|required (by|for)|needed (by|for)|"
    r"without (this|it)|was (failing|crashing|breaking|timing out)|broke|breaks)\b",
    re.IGNORECASE,
)
_TRAILER = re.compile(r"^[\w-]+(-by)?:\s", re.IGNORECASE)
_REVERT_LINE = re.compile(r"^This reverts commit [0-9a-f]+", re.IGNORECASE)
# Text aimed at an AI tool is untrusted input, not evidence.
_INJECTION = re.compile(
    r"\b(ignore (all |any )?(previous|prior|above) (instructions|prompts?)|"
    r"you are (an? )?(ai|assistant|language model)|system prompt|disregard .{0,20}instructions|"
    r"as an ai)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(])|\n\s*\n|\n(?=\s*[-*] )")


def sentences(text: str) -> list[str]:
    out: list[str] = []
    for block in _SENTENCE_SPLIT.split(text or ""):
        s = " ".join(block.split())  # join wrapped lines
        s = s.lstrip("-* ").strip()
        if s and not _TRAILER.match(s) and not _REVERT_LINE.match(s):
            out.append(s)
    return out


def is_injection(sentence: str) -> bool:
    return bool(_INJECTION.search(sentence))


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
_STOP = {"the", "and", "for", "this", "that", "with", "from", "not", "are", "was", "but",
         "def", "return", "self", "none", "true", "false", "import", "func", "var", "let",
         "const", "class", "public", "private", "static", "void", "int", "str", "new"}


def words(text: str) -> set[str]:
    """Lower-case identifier parts (snake and camel case split), minus stop words."""
    out = set()
    for w in _WORD.findall(text):
        for part in re.split(r"_|(?<=[a-z])(?=[A-Z])", w):
            p = part.lower()
            if len(p) >= 3 and p not in _STOP:
                out.add(p)
    return out


def stated_reason(text: str, context: set[str] | None = None) -> str | None:
    """A sentence in `text` that states a reason, verbatim, or None.

    With `context` (words from the code and file name), the reason sentence sharing the
    most words with it wins, so a multi-topic commit does not attach its first reason
    to every line it touched. Ties keep the earliest sentence.
    """
    best: tuple[int, int, str] | None = None
    for i, s in enumerate(sentences(text)):
        if is_injection(s) or not _REASON.search(s):
            continue
        score = len(words(s) & context) if context else 0
        if best is None or score > best[0]:
            best = (score, i, s)
        if not context:
            break
    return clean(best[2], max_len=MAX_QUOTE) if best else None


_CONSTRAINT = re.compile(
    r"\b(must( not)?|never|always|do not|don'?t|required|has to|needs? to|only (when|if))\b",
    re.IGNORECASE,
)


def constraints(text: str, *, limit: int = 3) -> list[str]:
    """Sentences that state a constraint ("must not", "never"), verbatim."""
    out: list[str] = []
    for s in sentences(text):
        if is_injection(s) or not _CONSTRAINT.search(s):
            continue
        q = clean(s, max_len=MAX_QUOTE)
        if q not in out:
            out.append(q)
        if len(out) >= limit:
            break
    return out


_ALTERNATIVE = re.compile(
    r"\b(instead of|rather than|alternatively|considered|we could have|an alternative)\b",
    re.IGNORECASE,
)


def alternatives(text: str, *, limit: int = 2) -> list[str]:
    out: list[str] = []
    for s in sentences(text):
        if not is_injection(s) and _ALTERNATIVE.search(s):
            out.append(clean(s, max_len=MAX_QUOTE))
        if len(out) >= limit:
            break
    return out


_COMMENT = re.compile(r"^\s*(#|//|/\*+|\*|--)\s?(.*?)\s*(\*/)?\s*$")


def comment_text(line: str) -> str | None:
    """The text of a whole-line or trailing comment, or None."""
    m = _COMMENT.match(line)
    if m:
        return m.group(2) or None
    for marker in ("  # ", " // "):
        if marker in line:
            return line.split(marker, 1)[1].strip() or None
    return None

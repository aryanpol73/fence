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
    r"without (this|it)|was (failing|crashing|breaking|timing out)|broke|breaks|"
    # purpose clauses: "... so a typo cannot weaken policy"
    r"so (that )?(\w+ ){0,5}(cannot|can't|won't|will not|never|doesn't|does not))\b",
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
# Reasons that carry no causal word, each narrow enough not to fire on description:
# - a result clause: "'now' is the commit time, so the same state scores the same."
_RESULT = re.compile(r",\s+so\s+(?!far\b|on\b|forth\b)\w", re.IGNORECASE)
# - a hazard: "an escape sequence would be executed by the reader's terminal."
_HAZARD = re.compile(
    r"\b(would|could|might)( otherwise| then)?( (be|get))? (executed|lost|corrupted|leaked|"
    r"exposed|dropped|duplicated|overwritten|truncated|deleted|injected|bypassed|"
    r"crash(es)?|fail|hang|deadlock|corrupt|leak)\b",
    re.IGNORECASE,
)
# - a consequence of the previous sentence: "... Fence would explain the wrong code. So
#   every removed range is compared first." It is quoted together with that sentence.
_CONSEQUENCE = re.compile(
    r"^(so(?! far\b)|hence|therefore|thus|that is why|this is why|that's why|"
    r"for (this|that) reason)\b",
    re.IGNORECASE,
)
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n|\n(?=\s*[-*] )")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(])")


def paragraphs(text: str) -> list[list[str]]:
    """Sentences grouped by paragraph or bullet item. Neighbours in one paragraph are
    each other's context; sentences in different paragraphs are unrelated."""
    out: list[list[str]] = []
    for para in _PARAGRAPH_SPLIT.split(text or ""):
        group = []
        for block in _SENTENCE_SPLIT.split(para):
            s = " ".join(block.split())  # join wrapped lines
            s = s.lstrip("-* ").strip()
            if s and not _TRAILER.match(s) and not _REVERT_LINE.match(s):
                group.append(s)
        if group:
            out.append(group)
    return out


def sentences(text: str) -> list[str]:
    return [s for para in paragraphs(text) for s in para]


def states_reason(sentence: str) -> bool:
    return bool(_REASON.search(sentence) or _RESULT.search(sentence)
                or _HAZARD.search(sentence))


def is_injection(sentence: str) -> bool:
    return bool(_INJECTION.search(sentence))


def stated_reason(text: str) -> str | None:
    """The first sentence in `text` that states a reason, verbatim, or None.

    Which text is about which code is decided before this call (see relevance.py);
    this function only finds the reason sentence.
    """
    for para in paragraphs(text):
        for i, s in enumerate(para):
            if is_injection(s):
                continue
            if states_reason(s):
                return clean(s, max_len=MAX_QUOTE)
            if i and _CONSEQUENCE.match(s) and not is_injection(para[i - 1]):
                # Still verbatim: the two sentences are adjacent in the source.
                return clean(f"{para[i - 1]} {s}", max_len=MAX_QUOTE)
    return None


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

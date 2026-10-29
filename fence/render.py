"""Human and JSON output. Verdict first, one-sentence reason, evidence, then detail.

Meaning never depends on colour: every coloured word is also a text label, so
output reads the same in plain terminals, CI logs and screen readers.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime

import typer

from fence.check.engine import CLEAR, KEEP, SAFE, VERIFY, CheckResult, Finding
from fence.decisions.build import WhyResult
from fence.decisions.model import SCHEMA, SCORE_NOTE, DecisionRecord, Evidence, Score
from fence.text import quoted

_COLORS = {KEEP: "red", VERIFY: "yellow", CLEAR: "green", SAFE: "cyan",
           "critical": "red", "high": "red", "medium": "yellow", "low": "green"}


class Out:
    def __init__(self, color: str = "auto", *, max_items: int = 10, verbose: bool = False) -> None:
        if color == "auto":
            self.color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        else:
            self.color = color == "always"
        self.max_items = max_items
        self.verbose = verbose

    def c(self, text: str, key: str | None = None, *, bold: bool = False) -> str:
        if not self.color:
            return text
        return typer.style(text, fg=_COLORS.get(key or text), bold=bold)


def _date(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d") if ts else "unknown date"


def _ev_line(e: Evidence) -> str:
    status = "" if e.retrieval_status == "ok" else f" ({e.retrieval_status.replace('_', ' ')})"
    ref = e.ref[:12] if e.type in ("commit", "revert") else e.ref
    link = f"  {e.url}" if e.url else ""
    return f"{e.id} {e.type} {ref}{status}{link}"


def _factors(s: Score, n: int = 3) -> str:
    return "; ".join(f"{f.points:+g} {f.reason}" for f in s.top(n))


def _reason(d: DecisionRecord) -> str:
    if d.stated_reason:
        src = next((e for e in d.evidence if e.id == d.stated_reason_evidence), None)
        where = f"{src.type} {src.ref[:12] if src.type == 'commit' else src.ref}" if src else "?"
        return f"{quoted(d.stated_reason)} (quoted from {where})"
    return d.reason_text


def decision_text(d: DecisionRecord, o: Out, *, full: bool = False) -> list[str]:
    s = d.strength
    commit = d.evidence[0]
    head = (f"{o.c(f'[{s.band.upper()} {s.value}]', s.band, bold=True)} {d.type} | grade {d.grade}"
            f" | confidence {s.confidence:.2f} | {d.id}")
    lines = [head, f"  Why: {_reason(d)}"]
    subject = (commit.quote or "").split("\n", 1)[0]
    lines.append(f"  Introduced {_date(d.introduced_at)} by {commit.author or 'unknown'} in "
                 f"{d.introduced_sha[:12]} \"{subject}\"")
    spans = ", ".join(f"{a.start_line}" + (f"-{a.end_line}" if a.end_line != a.start_line else "")
                      + (f" ({a.symbol})" if a.symbol else "") for a in d.anchors)
    lines.append(f"  Lines: {spans}" + (f" | status {d.status}" if d.status != "active" else ""))
    if d.constraints:
        lines.append("  Constraint: " + " | ".join(f'"{c}"' for c in d.constraints[:2]))
    lines.append(f"  Top factors: {_factors(s, 99 if full else 3)}")
    ev = d.evidence if full else d.evidence[:4]
    lines.append("  Evidence:" + ("" if ev else " none"))
    lines += [f"    {_ev_line(e)}" for e in ev]
    if len(d.evidence) > len(ev):
        lines.append(f"    ... {len(d.evidence) - len(ev)} more (use --verbose)")
    if s.what_would_change:
        lines.append("  Would change this: " + "; ".join(s.what_would_change))
    return lines


def why_text(r: WhyResult, o: Out, *, full: bool = False) -> str:
    t = r.target
    span = f"{t.start}" if t.start == t.end else f"{t.start}-{t.end}"
    sym = f" ({t.symbol})" if t.symbol else ""
    n = len(r.decisions)
    out = [f"{t.path}:{span}{sym} @ {t.rev[:12]} | {n} decision{'s' if n != 1 else ''}"]
    if r.unexplained_lines:
        out.append(f"{r.unexplained_lines} line(s) have no recoverable reason (grade D).")
    for d in r.decisions[: o.max_items]:
        out.append("")
        out += decision_text(d, o, full=full or o.verbose)
    if n > o.max_items:
        out.append(f"\n... {n - o.max_items} more decisions (raise --max-items)")
    if r.notes or r.decisions:
        out.append("")
        out += [f"Note: {x}" for x in r.notes]
    if r.decisions:
        out.append(f"Note: {SCORE_NOTE}")
    return "\n".join(out)


def ack_command(f: Finding, base: str, head_is_base: bool) -> str:
    rev = "" if head_is_base else f" --rev {base[:12]}"
    span = f"{f.start}" if f.start == f.end else f"{f.start}-{f.end}"
    return (f'fence ack {f.decision.id} --at {f.path}:{span}{rev} '
            f'--reason "<why it is safe to change>"')


def finding_text(f: Finding, o: Out, base: str, head_is_base: bool) -> list[str]:
    d, r = f.decision, f.risk
    span = f"{f.start}" if f.start == f.end else f"{f.start}-{f.end}"
    label = o.c(f"{f.verdict:<6}", f.verdict, bold=True)
    acked = " | ACKNOWLEDGED" if f.ack else ""
    lines = [f"{label} {f.path}:{span}  {f.kind}  risk {r.value} ({r.band}) | "
             f"evidence confidence {r.confidence:.2f} | grade {d.grade}{acked}"]
    preview = f.removed_preview.split("\n")
    lines.append(f"  Change: {f.kind_reason}: " + preview[0].strip()
                 + (f" (+{len(preview) - 1} lines)" if len(preview) > 1 else ""))
    lines.append(f"  Why it matters: {_reason(d)}")
    lines.append(f"  Decision {d.id}: {d.type}, introduced {_date(d.introduced_at)} in "
                 f"{d.introduced_sha[:12]}")
    lines.append("  Evidence: " + ", ".join(
        f"{e.type} {e.ref[:12] if e.type in ('commit', 'revert') else e.ref}"
        + ("" if e.retrieval_status == "ok" else f" ({e.retrieval_status.replace('_', ' ')})")
        for e in d.evidence[:5]))
    lines.append(f"  Factors: {_factors(r)}")
    if f.ack:
        lines.append(f'  Acknowledged by {f.ack.who} on {f.ack.when[:10]}: "{f.ack.reason}"')
    elif f.verdict == KEEP:
        lines.append("  Next: check with the decision's author or reviewers, or acknowledge:")
        lines.append(f"    {ack_command(f, base, head_is_base)}")
    elif f.verdict == VERIFY:
        lines.append("  Next: confirm the reason no longer applies before merging.")
    elif f.verdict == SAFE:
        lines.append("  Next: possibly obsolete; verify the evidence, then remove.")
    return lines


def check_text(r: CheckResult, o: Out) -> str:
    head_is_base = r.head is None
    counts = {v: sum(1 for f in r.findings if f.verdict == v) for v in (KEEP, VERIFY, SAFE)}
    out = [f"{o.c(r.verdict, r.verdict, bold=True)}: "
           + (_summary_sentence(r, counts) if r.findings else "no meaningful decision affected.")]
    shown = r.findings[: o.max_items]
    for f in shown:
        out.append("")
        out += finding_text(f, o, r.base, head_is_base)
    if len(r.findings) > len(shown):
        out.append(f"\n... {len(r.findings) - len(shown)} more findings (raise --max-items)")
    out.append("")
    out.append(f"Checked {r.blocks_checked} change block(s) in {r.files_checked} file(s) "
               f"against {r.base[:12]} in {r.seconds:.2f}s | exit {r.exit_code}"
               + (" (bypassed by env)" if r.bypassed else ""))
    for path, g in r.ghosts:
        out.append(f"Ghost fence: {path} re-adds code (similarity {g.similarity}) that was "
                   f"removed in {g.removed_in[:12]} \"{g.subject}\" from {g.path}.")
    out += [f"Note: {x}" for x in r.notes]
    if r.findings:
        out.append(f"Note: {SCORE_NOTE}")
    return "\n".join(out)


def _summary_sentence(r: CheckResult, counts: dict[str, int]) -> str:
    parts = [f"{n} {v}" for v, n in counts.items() if n]
    top = r.findings[0]
    return (f"{', '.join(parts)}. Top: {top.path}:{top.start} touches a {top.decision.type} "
            f"decision ({top.decision.grade}).")


COMMENT_MARKER = "<!-- fence:sticky -->"


def check_markdown(r: CheckResult, ctx) -> str:
    """PR comment / job summary. Only meaningful findings; links where we have them."""
    out = [COMMENT_MARKER, f"### Fence check: **{r.verdict}**", ""]
    if not r.findings:
        out.append("No meaningful decision affected by this change.")
    for f in r.findings[:15]:
        d = f.decision
        commit = d.introduced_sha[:12]
        url = ctx.commit_url(d.introduced_sha) if ctx else None
        commit_md = f"[{commit}]({url})" if url else f"`{commit}`"
        acked = " (acknowledged)" if f.ack else ""
        out.append(f"**{f.verdict}**{acked} `{f.path}:{f.start}` | {f.kind} | risk {f.risk.value} "
                   f"({f.risk.band}) | grade {d.grade} | confidence {f.risk.confidence:.2f}")
        out += ["", f"> {_reason(d).replace(chr(10), ' ')}", ""]
        out.append(f"- Decision `{d.id}` ({d.type}) from {commit_md}")
        out.append(f"- Evidence: {_md_evidence(d, 4)}")
        out.append(f"- Factors: {_factors(f.risk)}")
        if not f.ack and f.verdict == KEEP:
            out.append(f"<sub>To proceed: `{ack_command(f, r.base, r.head is None)}`</sub>")
        out.append("")
    if len(r.findings) > 15:
        out.append(f"... and {len(r.findings) - 15} more.")
    out.append(f"<sub>{r.blocks_checked} change blocks in {r.files_checked} files against "
               f"`{r.base[:12]}`. Repository text above is quoted evidence. "
               f"{SCORE_NOTE}</sub>")
    return "\n".join(out)


def _md_evidence(d: DecisionRecord, n: int) -> str:
    def one(e: Evidence) -> str:
        label = f"{e.type} {e.ref[:12] if e.type in ('commit', 'revert') else e.ref}"
        status = "" if e.retrieval_status == "ok" else f" ({e.retrieval_status.replace('_', ' ')})"
        return (f"[{label}]({e.url})" if e.url else label) + status
    return ", ".join(one(e) for e in d.evidence[:n]) or "none"


def why_markdown(r: WhyResult, ctx) -> str:
    """The same fields as the text output, for PR descriptions, issues and docs."""
    t = r.target
    span = f"{t.start}" if t.start == t.end else f"{t.start}-{t.end}"
    sym = f" ({t.symbol})" if t.symbol else ""
    out = [f"### Fence why: `{t.path}:{span}`{sym} @ `{t.rev[:12]}`", ""]
    if r.unexplained_lines:
        out += [f"{r.unexplained_lines} line(s) have no recoverable reason (grade D).", ""]
    for d in r.decisions:
        s = d.strength
        url = ctx.commit_url(d.introduced_sha) if ctx else None
        commit = d.introduced_sha[:12]
        commit_md = f"[{commit}]({url})" if url else f"`{commit}`"
        lines = ", ".join(f"{a.start_line}" + (f"-{a.end_line}" if a.end_line != a.start_line
                                               else "") + (f" ({a.symbol})" if a.symbol else "")
                          for a in d.anchors)
        out.append(f"**{s.band.upper()} {s.value}** {d.type} | grade {d.grade} | "
                   f"confidence {s.confidence:.2f} | `{d.id}`")
        out += ["", f"> {_reason(d).replace(chr(10), ' ')}", ""]
        out.append(f"- Introduced {_date(d.introduced_at)} by "
                   f"{d.evidence[0].author or 'unknown'} in {commit_md}")
        out.append(f"- Lines: {lines}" + (f" | status {d.status}" if d.status != "active" else ""))
        if d.constraints:
            out.append("- Constraint: " + " | ".join(f'"{c}"' for c in d.constraints[:2]))
        out.append(f"- Factors: {_factors(s)}")
        out.append(f"- Evidence: {_md_evidence(d, 6)}")
        if s.what_would_change:
            out.append("- Would change this: " + "; ".join(s.what_would_change))
        out.append("")
    out += [f"Note: {x}" for x in r.notes]
    out.append(f"<sub>Repository text above is quoted evidence. {SCORE_NOTE}</sub>")
    return "\n".join(out)


# JSON ---------------------------------------------------------------------------

def why_json(r: WhyResult) -> dict:
    return {
        "schema": SCHEMA, "kind": "why",
        "target": asdict(r.target),
        "decisions": [d.to_dict() for d in r.decisions],
        "unexplained_lines": r.unexplained_lines,
        "notes": r.notes, "score_note": SCORE_NOTE,
    }


def finding_dict(f: Finding, base: str, head_is_base: bool) -> dict:
    return {
        "verdict": f.verdict, "path": f.path, "start": f.start, "end": f.end, "kind": f.kind,
        "kind_reason": f.kind_reason, "risk": {**asdict(f.risk),
                                               "top_factors": [asdict(x) for x in f.risk.top()]},
        "decision": f.decision.to_dict(), "decision_fingerprint": f.decision_fingerprint,
        "removed_preview": f.removed_preview, "lost_signals": list(f.lost_signals),
        "ack": asdict(f.ack) if f.ack else None,
        "ack_command": None if f.ack else ack_command(f, base, head_is_base),
    }


def check_json(r: CheckResult) -> dict:
    return {
        "schema": SCHEMA, "kind": "check", "verdict": r.verdict, "exit_code": r.exit_code,
        "base": r.base, "head": r.head, "bypassed": r.bypassed,
        "files_checked": r.files_checked, "blocks_checked": r.blocks_checked,
        "seconds": r.seconds, "notes": r.notes, "score_note": SCORE_NOTE,
        "findings": [finding_dict(f, r.base, r.head is None) for f in r.findings],
        "ghosts": [{"path": p, **asdict(g)} for p, g in r.ghosts],
    }


def dumps(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=False, ensure_ascii=False, default=str)

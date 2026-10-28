"""Lines of code -> Decision Records, from blame plus the index. Deterministic.

One Decision Record is one introducing commit's change to one file (identified by
the file's path at that commit, so the id survives later renames). Its anchors are
the current line ranges that commit still owns.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace

from fence.anchors.fingerprint import containment, shingles
from fence.check.diff import DiffError, parse_diff
from fence.context import Context
from fence.decisions import obsolete, reasons
from fence.decisions.model import Anchor, DecisionRecord, Evidence
from fence.git.blame import BlameLine, blame
from fence.git.log import log_line_range
from fence.git.runner import GitError
from fence.globs import match_any
from fence.index.store import CommitRow
from fence.lang import enclosing_symbol, plugin_for, tokens_for
from fence.scoring.strength import StrengthInputs, strength
from fence.signals.catalog import (
    DECISION_TYPES,
    LABEL_SIGNALS,
    TEXT_SIGNALS,
    extract_labels,
    extract_text,
)
from fence.signals.code import CODE_SIGNALS, match_code
from fence.text import clean

TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs)/|_test\.(go|py)$|(^|/)test_[^/]+\.py$|"
    r"\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.java$"
)
VENDORED = ("vendor/**", "third_party/**", "node_modules/**", "**/generated/**", "**/*.pb.go",
            "**/*_pb2.py", "**/*.min.js")
LINK_SIGNALS = {"text.closes_issue", "text.issue_ref", "text.ticket", "text.incident_url"}
FIX_SIGNALS = {"text.fix", "text.regression", "text.hotfix"}
_TEXT_TYPE = {s.id: s.decision_type for s in TEXT_SIGNALS + LABEL_SIGNALS}
_CODE_WEIGHT = {s.id: s.weight for s in CODE_SIGNALS}
_CODE_TYPE = {s.id: s.decision_type for s in CODE_SIGNALS}
MAX_LATER = 20


class WhyError(Exception):
    """The target cannot be resolved (bad path, line out of range, unknown symbol)."""


@dataclass
class Target:
    path: str
    start: int
    end: int
    symbol: str | None
    rev: str


@dataclass
class WhyResult:
    target: Target
    decisions: list[DecisionRecord]
    notes: list[str] = field(default_factory=list)
    unexplained_lines: int = 0


def resolve_target(
    ctx: Context, path: str, start: int | None = None, end: int | None = None, *,
    symbol: str | None = None, rev: str = "HEAD",
) -> tuple[Target, str]:
    rev_sha = ctx.repo.verify_ref(rev)
    rel = ctx.repo.safe_relpath(path)
    try:
        text = ctx.repo.read_blob(rev_sha, rel, max_bytes=ctx.cfg.index.max_file_size)
    except GitError as e:
        raise WhyError(f"cannot read {rel} at {rev}: {e}") from None
    total = len(text.splitlines())
    if total == 0:
        raise WhyError(f"{rel} is empty at {rev}")
    if symbol:
        plugin = plugin_for(rel, ctx.cfg.index.languages)
        syms = plugin.symbols(text) if plugin else None
        if syms is None:
            raise WhyError(f"no symbols available for {rel} (unsupported language or parse error)")
        hits = [s for s in syms if s.qualname == symbol or s.qualname.endswith("." + symbol)]
        if not hits:
            raise WhyError(f"symbol {symbol!r} not found in {rel}")
        s = hits[0]
        return Target(rel, s.start_line, s.end_line, s.qualname, rev_sha), text
    if start is None:
        start, end = 1, total  # whole file
    elif end is None:
        end = start
    if not (1 <= start <= end <= total):
        raise WhyError(f"line range {start}-{end} is outside {rel} (1-{total})")
    sym = enclosing_symbol(rel, text, start)
    return Target(rel, start, end, sym.qualname if sym else None, rev_sha), text


def format_only_shas(ctx: Context, paths: list[str]) -> list[str]:
    marks = ",".join("?" * len(paths))
    rows = ctx.store.conn.execute(
        "SELECT DISTINCT c.sha FROM commits c JOIN file_changes f ON f.sha = c.sha "  # noqa: S608
        f"WHERE c.format_only = 1 AND f.path IN ({marks})", paths,
    )
    return [r["sha"] for r in rows]


def why(
    ctx: Context, path: str, start: int | None = None, end: int | None = None, *,
    symbol: str | None = None, rev: str = "HEAD", deep: bool = True,
) -> WhyResult:
    target, text = resolve_target(ctx, path, start, end, symbol=symbol, rev=rev)
    lines = text.split("\n")
    history = ctx.store.path_history(target.path)
    blamed = blame(
        ctx.repo, target.rev, target.path, [(target.start, target.end)],
        extra_ignore=format_only_shas(ctx, history),
    )
    result = WhyResult(target, [])
    if ctx.shallow:
        result.notes.append("shallow clone: lines older than the boundary cannot be explained")
    if ctx.offline:
        result.notes.append("local-git mode: PR, issue and review text not available "
                            "(set GITHUB_TOKEN and run `fence index`)")
    result.decisions = decisions_for_lines(ctx, target, lines, blamed, history, deep=deep)
    result.unexplained_lines = sum(
        len(range(a.start_line, a.end_line + 1))
        for d in result.decisions if d.grade == "D" for a in d.anchors
    )
    return result


def decisions_for_lines(
    ctx: Context, target: Target, lines: list[str], blamed: list[BlameLine],
    history: list[str], *, deep: bool = True,
) -> list[DecisionRecord]:
    groups: dict[tuple[str, str], list[BlameLine]] = {}
    for bl in blamed:
        groups.setdefault((bl.sha, bl.filename), []).append(bl)
    for _ in range(3):  # follow moves at most three hops back
        moved = False
        for key in list(groups):
            followed = _follow_move(ctx, key[0], key[1], groups[key])
            if followed:
                del groups[key]
                for bl in followed:
                    groups.setdefault((bl.sha, bl.filename), []).append(bl)
                moved = True
        if not moved:
            break
    rows = ctx.store.commits(sha for sha, _ in groups)
    records = [
        _record(ctx, target, lines, sha, filename, bls, rows.get(sha), history, deep=deep)
        for (sha, filename), bls in groups.items()
    ]
    records.sort(key=lambda r: (-r.strength.value, -r.introduced_at, r.id))
    return records


def _follow_move(ctx: Context, sha: str, filename: str,
                 bls: list[BlameLine]) -> list[BlameLine] | None:
    """If `sha` moved these lines here from another file (which git blame -C can miss),
    return blame lines from that file at `sha`'s parent, keeping current line numbers."""
    row = ctx.store.commit(sha)
    if row is None or len(row.parents) != 1:
        return None
    sources = [c.path if c.status != "R" else c.old_path for c in ctx.store.changes_for(sha)
               if (c.old_path or c.path) != filename and (c.deleted or 0) > 0]
    sources = [s for s in sources if s][:20]
    group_tokens = tokens_for(filename, "\n".join(bl.content for bl in bls))
    exact = {bl.content.strip() for bl in bls}
    distinctive = all(len(t) >= 15 for t in exact)
    if not sources or (len(group_tokens) < 8 and not distinctive):
        return None
    digest = hashlib.sha256(chr(0).join(group_tokens).encode()).hexdigest()[:12]
    key = f"move:{sha}:{filename}:{digest}"
    if (cached := ctx.store.cache_get(key)) is not None and not cached:
        return None
    try:
        text = ctx.repo.run(["show", "--format=", "-U0", "--no-color", "--no-ext-diff",
                             "--src-prefix=a/", "--dst-prefix=b/", sha, "--", *sources])
        files = parse_diff(text)
    except (GitError, DiffError):
        return None
    group_sh = shingles(group_tokens)
    for f in files:
        if f.old_path is None:
            continue
        for b in f.blocks:
            removed_text = "\n".join(t for _, t in b.removed)
            same = exact <= {t.strip() for _, t in b.removed}
            if not (same and distinctive) and containment(
                    group_sh, shingles(tokens_for(f.old_path, removed_text))) < 0.8:
                continue
            by_text: dict[str, int] = {}
            for n, t in b.removed:
                by_text.setdefault(t.strip(), n)
            src_lines = {bl.final_line: by_text.get(bl.content.strip()) for bl in bls}
            wanted = sorted({n for n in src_lines.values() if n})
            if not wanted:
                continue
            try:
                older = blame(ctx.repo, row.parents[0], f.old_path,
                              [(n, n) for n in wanted][:200])
            except GitError:
                return None
            by_line = {bl.final_line: bl for bl in older}
            out = []
            for bl in bls:
                src = src_lines.get(bl.final_line)
                out.append(replace(by_line[src], final_line=bl.final_line, content=bl.content)
                           if src in by_line else bl)
            return out
    with ctx.store.transaction():
        ctx.store.cache_put(key, 0)
    return None


def decision_id(sha: str, path: str) -> str:
    return "D" + hashlib.sha256(f"{sha}\x00{path}".encode()).hexdigest()[:10]


def _ranges(nums: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for n in sorted(set(nums)):
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def _record(
    ctx: Context, target: Target, lines: list[str], sha: str, filename: str,
    bls: list[BlameLine], row: CommitRow | None, history: list[str], *, deep: bool,
) -> DecisionRecord:
    first = bls[0]
    message = row.message if row else first.summary
    ev: list[Evidence] = []
    authors = {first.author}
    ctx_words = reasons.words(" ".join(bl.content for bl in bls) + " " + filename)

    def add(**kw) -> Evidence:
        e = Evidence(id=f"E{len(ev) + 1}", **kw)
        ev.append(e)
        return e

    commit_ev = add(
        type="commit", ref=sha, url=ctx.commit_url(sha), timestamp=first.author_time,
        author=clean(first.author, max_len=80),
        quote=clean(message, max_len=600, keep_newlines=True),
        retrieval_status=("orphaned" if row and row.orphaned else "ok" if row else "not_indexed"),
    )
    signals = (ctx.store.signals_for(sha) if row
               else extract_text(message))
    sig_ids = {s for s, _ in signals}

    stated: str | None = None
    stated_ev: str | None = None
    grade = "D"
    constraints: list[str] = []
    alternatives: list[str] = []

    # PR, issues, reviews (grade A when they state a reason).
    pr = row.pr_number if row else None
    if pr is None and deep and not first.boundary:
        pr = _find_merge_pr(ctx, sha, target.rev)
    if pr is not None:
        prow = ctx.store.conn.execute("SELECT * FROM prs WHERE number = ?", (pr,)).fetchone()
        if prow and prow["state"] != "unreachable":
            body = f"{prow['title'] or ''}\n\n{prow['body'] or ''}"
            pe = add(type="pr", ref=f"#{pr}", url=prow["url"] or ctx.pr_url(pr), timestamp=None,
                     author=prow["author"], quote=clean(prow["title"] or "", max_len=200))
            pr_sigs = extract_text(body, skip_refs=frozenset({f"#{pr}"}))
            sig_ids |= {s for s, _ in pr_sigs}
            signals = [*signals, *(x for x in pr_sigs if x[0] in LINK_SIGNALS and x not in signals)]
            sig_ids |= set(extract_labels(json.loads(prow["labels"] or "[]")))
            for reviewer in json.loads(prow["reviewers"] or "[]"):
                authors.add(reviewer)
            if (q := reasons.stated_reason(prow["body"] or "", ctx_words)) and not stated:
                stated, stated_ev, grade = q, pe.id, "A"
            constraints += reasons.constraints(prow["body"] or "")
            alternatives += reasons.alternatives(prow["body"] or "")
            for rc in ctx.store.conn.execute(
                "SELECT * FROM review_comments WHERE pr_number = ? ORDER BY id", (pr,)
            ):
                q = reasons.stated_reason(rc["body"] or "")
                if q:
                    re_ = add(type="review_comment", ref=f"#{pr}", url=rc["url"], timestamp=None,
                              author=rc["author"], quote=q)
                    if not stated:
                        stated, stated_ev, grade = q, re_.id, "A"
        else:
            add(type="pr", ref=f"#{pr}", url=ctx.pr_url(pr), timestamp=None, author=None,
                quote=None, retrieval_status="unreachable" if prow else "not_fetched")

    links: list[str] = []
    for sid, detail in signals:
        if sid not in LINK_SIGNALS or detail in links:
            continue
        links.append(detail)
        if sid in ("text.closes_issue", "text.issue_ref") and detail.startswith("#"):
            n = int(detail[1:])
            irow = ctx.store.conn.execute("SELECT * FROM issues WHERE number = ?", (n,)).fetchone()
            if irow and irow["retrieval_status"] == "ok":
                ie = add(type="issue", ref=detail, url=irow["url"], timestamp=None, author=None,
                         quote=clean(irow["title"] or "", max_len=200))
                sig_ids |= {s for s, _ in extract_text(f"{irow['title']}\n{irow['body']}")}
                q = reasons.stated_reason(irow["body"] or "")
                if q and not stated and sid == "text.closes_issue":
                    stated, stated_ev, grade = q, ie.id, "A"
            else:
                add(type="issue", ref=detail, url=ctx.issue_url(n), timestamp=None, author=None,
                    quote=None, retrieval_status=(irow["retrieval_status"] if irow
                                                  else "not_fetched"))
        elif sid == "text.ticket":
            add(type="ticket", ref=detail, url=None, timestamp=None, author=None, quote=None,
                retrieval_status="not_fetched")
        elif sid == "text.incident_url":
            add(type="incident_link", ref=detail, url=detail, timestamp=None, author=None,
                quote=None, retrieval_status="not_fetched")
    if pr is not None:
        links.append(f"PR #{pr}")

    # Commit message (grade B).
    if not stated and (q := reasons.stated_reason(message, ctx_words)):
        stated, stated_ev, grade = q, commit_ev.id, "B"
    constraints += reasons.constraints(message)
    alternatives += reasons.alternatives(message)

    # Code: the owned lines plus comment lines directly above the first one.
    owned = [bl.final_line for bl in bls]
    spans = _ranges(owned)
    code_lines = [lines[n - 1] for n in owned if n - 1 < len(lines)]
    above = []
    n = spans[0][0] - 1
    while n >= 1 and len(above) < 4 and reasons.comment_text(lines[n - 1]) is not None:
        above.insert(0, (n, lines[n - 1]))
        n -= 1
    code_sigs = match_code(code_lines + [t for _, t in above])
    for ln, t in above + [(bl.final_line, bl.content) for bl in bls]:
        c = reasons.comment_text(t)
        if not c or reasons.is_injection(c):
            continue
        if not stated and (q := reasons.stated_reason(c)):
            ce = add(type="code_comment", ref=f"{target.path}:{ln}@{target.rev[:12]}", url=None,
                     timestamp=None, author=None, quote=q)
            stated, stated_ev, grade = q, ce.id, "B"
        constraints += reasons.constraints(c, limit=1)

    # Reverts.
    reverted = ctx.store.reverted_by(sha)
    reverters = ctx.store.reverts_of(sha, as_of=ctx.as_of)
    if reverted:
        add(type="revert", ref=reverted, url=ctx.commit_url(reverted), timestamp=None,
            author=None, quote="this commit reverts " + reverted[:12])
    for r in reverters:
        add(type="revert", ref=r, url=ctx.commit_url(r), timestamp=None, author=None,
            quote="reverted by " + r[:12] + " and later restored")

    # Tests added alongside.
    test_paths = [c.path for c in ctx.store.changes_for(sha) if TEST_PATH.search(c.path)] \
        if row else []
    for tp in test_paths[:3]:
        add(type="test", ref=f"{tp}@{sha[:12]}", url=None, timestamp=None, author=None,
            quote=clean(tp, max_len=200))

    # Line history: later touches, regression chain, authors.
    later: list[str] = []
    regression = 0
    if deep:
        try:
            touches = log_line_range(ctx.repo, target.rev, target.path, spans[0][0],
                                     spans[-1][1], max_count=MAX_LATER)
        except GitError:
            touches = []
        touch_rows = ctx.store.commits(t.sha for t in touches)
        for t in touches:
            if t.sha == sha:
                continue
            authors.add(t.author)
            tr = touch_rows.get(t.sha)
            if tr and tr.format_only:
                continue
            tsig_rows = ctx.store.signals_for(t.sha) if tr else extract_text(t.subject)
            tsigs = {s for s, _ in tsig_rows}
            if tsigs & FIX_SIGNALS:
                regression += 1
            if tr and tr.author_time > first.author_time:
                later.append(t.sha)

    # Type and grade C.
    dtype = _decision_type(sig_ids, code_sigs, reverted is not None)
    inferred = None
    # Grade C needs a history signal (message, label, revert). The code's shape alone
    # (it has a lock) says what the code does, not why it was added.
    history_types = {_TEXT_TYPE[s] for s in sig_ids if _TEXT_TYPE.get(s)}
    if reverted:
        history_types.add("revert")
    if grade == "D" and dtype in history_types and dtype not in ("unknown", "cleanup"):
        grade = "C"
        cues = [d for s, d in signals if _TEXT_TYPE.get(s) == dtype][:2]
        cue = f" ({', '.join(repr(clean(c, max_len=40)) for c in cues)})" if cues else ""
        inferred = f"Inferred: looks like a {dtype} change{cue}; no reason is stated."
    elif grade == "D" and reverters:
        # A test alongside is a strength factor, not a reason: it no longer lifts D to C
        # (backtest: it graded most of a well-tested repo C without predicting anything).
        grade = "C"
        inferred = "Inferred: was reverted and then restored; no reason is stated."

    later_count = _later_commits_to_file(ctx, history, row)
    inputs = StrengthInputs(
        decision_type=dtype, grade=grade, links=links,
        positive_text_signals=sorted(s for s in sig_ids
                                     if s.startswith(("text.", "label.")) and _weight_positive(s)),
        code_signals=[(s, _CODE_WEIGHT[s]) for s in sorted({s for s, _ in code_sigs})],
        age_days=max(0.0, (_now(ctx, target.rev) - first.author_time) / 86400),
        later_commits_to_file=later_count, regression_fixes=regression,
        revert_involved=bool(reverted or reverters), test_added=bool(test_paths),
        distinct_authors=len(authors), bulk=bool(row and row.bulk),
        vendored=match_any(target.path, VENDORED),
        automated="text.automated" in sig_ids, refactor="text.refactor" in sig_ids,
        shallow=ctx.shallow or first.boundary, not_indexed=row is None,
        pr_unfetched=any(e.type == "pr" and e.retrieval_status == "not_fetched" for e in ev),
        offline=ctx.offline,
    )
    score = strength(inputs, ctx.cfg)

    plugin = plugin_for(target.path)
    anchors = []
    for a, b in spans:
        sym = enclosing_symbol(target.path, "\n".join(lines), a) if plugin else None
        anchors.append(Anchor(target.path, a, b, sym.qualname if sym else None,
                              plugin.name if plugin else None, tuple(history)))
    status = "orphaned" if row and row.orphaned else "active"
    if deep and status == "active":
        stale = obsolete.caller_findings(ctx.repo, target.rev, anchors[0].symbol, target.path)
        stale += obsolete.flag_findings(ctx.repo, target.rev, code_lines, target.path)
        if row and dtype in ("compatibility", "dependency-pin", "bugfix"):
            stale += obsolete.dependency_findings(ctx.repo, sha, target.rev, message,
                                                  ctx.cache)
        for s in stale:
            add(type="obsolescence", ref=target.rev[:12], url=None, timestamp=None, author=None,
                quote=f"possibly obsolete, verify: {s}")
        if stale:
            status = "stale-suspected"
    return DecisionRecord(
        id=decision_id(sha, filename), anchors=anchors, type=dtype, grade=grade,  # type: ignore[arg-type]
        stated_reason=stated, stated_reason_evidence=stated_ev, inferred_reason=inferred,
        constraints=list(dict.fromkeys(constraints))[:3],
        alternatives_considered=list(dict.fromkeys(alternatives))[:2], evidence=ev,
        introduced_sha=sha, introduced_at=first.author_time, introduced_path=filename,
        last_touched_at=first.author_time, authors=sorted(authors), status=status,
        strength=score, signals=sorted(set(signals) | {(s, d) for s, d in code_sigs}),
        later_touches=later, pr_number=pr,
        supersedes=[reverted] if reverted else [],
    )


def _weight_positive(sid: str) -> bool:
    return any(s.id == sid and s.weight > 0 for s in TEXT_SIGNALS + LABEL_SIGNALS)


def _decision_type(sig_ids: set[str], code_sigs: list[tuple[str, str]], is_revert: bool) -> str:
    if is_revert:
        return "revert"
    types = {_TEXT_TYPE[s] for s in sig_ids if _TEXT_TYPE.get(s)}
    positive = types - {"cleanup"}
    for t in DECISION_TYPES:
        if t in positive:
            return t
    code_types = {_CODE_TYPE[s] for s, _ in code_sigs if _CODE_TYPE.get(s)}
    for t in DECISION_TYPES:
        if t in code_types and t != "deliberate-design":
            return t
    if "cleanup" in types:
        return "cleanup"
    if any(s == "code.warning_comment" for s, _ in code_sigs):
        return "deliberate-design"
    return "unknown"


def _find_merge_pr(ctx: Context, sha: str, rev: str) -> int | None:
    key = f"merge-pr:{sha}:{rev}"
    cached = ctx.store.cache_get(key)
    if cached is not None:
        return cached or None  # 0 means "looked, none"
    pr = None
    try:
        out = ctx.repo.run(["rev-list", "--ancestry-path", "--merges", f"{sha}..{rev}"])
        merges = out.split()
        if merges:
            row = ctx.store.commit(merges[-1])  # the oldest merge on the path brought it in
            pr = row.pr_number if row else None
    except GitError:
        pr = None
    with ctx.store.transaction():
        ctx.store.cache_put(key, pr or 0)
    return pr


def _later_commits_to_file(ctx: Context, history: list[str], row: CommitRow | None) -> int:
    if row is None:
        return 0
    marks = ",".join("?" * len(history))
    seq = ctx.store.conn.execute("SELECT seq FROM commits WHERE sha = ?", (row.sha,)).fetchone()
    if not seq:
        return 0
    r = ctx.store.conn.execute(
        "SELECT COUNT(DISTINCT c.sha) AS n FROM commits c JOIN file_changes f ON f.sha = c.sha "  # noqa: S608
        f"WHERE f.path IN ({marks}) AND c.seq > ? AND c.orphaned = 0 AND c.format_only = 0"
        " AND c.committer_time <= ?",
        [*history, seq["seq"], ctx.as_of if ctx.as_of is not None else 2**62],
    ).fetchone()
    return int(r["n"])


def _now(ctx: Context, rev: str) -> int:
    """'Now' is the revision's commit time, so results depend only on repo state."""
    key = f"time:{rev}"
    if (t := ctx.store.cache_get(key)) is not None:
        return int(t)
    t = int(ctx.repo.run(["show", "-s", "--format=%ct", rev]).strip())
    ctx.store.cache_put(key, t)  # autocommit: the connection is not in a transaction here
    return t

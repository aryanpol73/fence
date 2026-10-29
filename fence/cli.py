"""Typer entry points. Exit codes: 0 ok, 1 error, 2 policy threshold exceeded,
3 acknowledgement required, 4 index unavailable."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer

from fence import __version__
from fence.check.diff import DiffError, DiffSpec, merge_base
from fence.check.engine import EXIT_ERROR, EXIT_NO_INDEX, _decision_fingerprint, check
from fence.config import ConfigError, load_config
from fence.context import Context
from fence.decisions.build import WhyError, why
from fence.git.runner import GitError
from fence.index.formatting import detect_format_only
from fence.index.indexer import index_repo, needs_index
from fence.index.store import IndexUnavailable, db_path_for
from fence.policy import acks as ackmod
from fence.render import Out, check_json, check_text, dumps, why_json, why_markdown, why_text

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Fence: know why code exists before you remove it.")

RepoOpt = Annotated[Path, typer.Option("--repo", "-C", help="Repository path.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output (schema fence/v1).")]
QuietOpt = Annotated[bool, typer.Option("--quiet", "-q", help="Only the exit code and errors.")]
NoColorOpt = Annotated[bool, typer.Option("--no-color", help="Never colour output.")]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v",
                                          help="Show every factor and evidence.")]

_TARGET = re.compile(r"^(?P<path>.+?):(?P<start>\d+)(?:-(?P<end>\d+))?$")


@app.callback()
def main() -> None:
    """Fence: know why code exists before you remove it."""
    # An explicit callback keeps Typer in multi-command mode, so `fence version`
    # stays a subcommand rather than becoming the root command.


def _fail(msg: str, code: int = EXIT_ERROR) -> typer.Exit:
    typer.echo(f"fence: {msg}", err=True)
    return typer.Exit(code)


def _open(repo: Path, *, create: bool = True) -> Context:
    try:
        return Context.open(repo, create=create)
    except ConfigError as e:
        raise _fail(str(e)) from None
    except IndexUnavailable as e:
        raise _fail(str(e), EXIT_NO_INDEX) from None
    except GitError as e:
        raise _fail(f"{e} (is {repo} inside a git repository?)") from None


def _out(ctx: Context, no_color: bool, verbose: bool) -> Out:
    color = "never" if no_color else ctx.cfg.output.color
    return Out(color, max_items=ctx.cfg.output.max_items, verbose=verbose)


def parse_target(target: str) -> tuple[str, int | None, int | None]:
    m = _TARGET.match(target)
    if not m:
        return target, None, None
    start = int(m["start"])
    return m["path"], start, int(m["end"]) if m["end"] else None


def _ensure_indexed(ctx: Context, quiet: bool) -> None:
    """Index first when needed, so `fence why` works in one step and never explains new
    commits from their subject line alone."""
    if needs_index(ctx.repo, ctx.store, ctx.cfg):
        if not quiet:
            empty = ctx.store.commit_count(include_orphaned=True) == 0
            typer.echo("fence: no index yet; indexing history first..." if empty
                       else "fence: indexing new commits...", err=True)
        index_repo(ctx.repo, ctx.store, ctx.cfg, detect_format=detect_format_only)


@app.command()
def version() -> None:
    """Print the Fence version."""
    typer.echo(__version__)


@app.command()
def index(
    repo: RepoOpt = Path("."), reindex: Annotated[bool, typer.Option(
        "--reindex", help="Delete the index and rebuild it from scratch.")] = False,
    json_out: JsonOpt = False, quiet: QuietOpt = False,
) -> None:
    """Index history incrementally (only new commits are processed)."""
    if reindex:
        ctx = _open(repo)
        path = ctx.store.path
        ctx.close()
        if path:
            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
    ctx = _open(repo)

    def progress(n: int) -> None:
        if not quiet and not json_out:
            typer.echo(f"  indexed {n} commits...", err=True)

    try:
        rep = index_repo(ctx.repo, ctx.store, ctx.cfg, detect_format=detect_format_only,
                         progress=progress)
    except GitError as e:
        raise _fail(str(e)) from None
    from fence.index.ghosts import scan_tombstones

    try:
        rep.notes.append(f"{scan_tombstones(ctx.repo, ctx.store, ctx.cfg)} new ghost-fence "
                         "tombstones recorded")
    except GitError as e:
        rep.notes.append(f"ghost-fence scan skipped: {e}")
    gh = None
    if ctx.github_token and ctx.slug:
        from fence.github.sync import sync

        gh = sync(ctx)
    if json_out:
        typer.echo(dumps({"schema": "fence/v1", "kind": "index", **rep.__dict__,
                          "github": gh.__dict__ if gh else None}))
        return
    if gh and not quiet:
        typer.echo(f"GitHub: {gh.prs} PRs, {gh.issues} issues, {gh.review_comments} review "
                   f"comments fetched ({gh.requests} requests)"
                   + (f"; stopped: {gh.stopped}" if gh.stopped else ""))
    if quiet:
        return
    typer.echo(f"Indexed {rep.indexed} new commit(s) in {rep.seconds:.1f}s; "
               f"{rep.total} in index.")
    for n in rep.notes:
        typer.echo(f"Note: {n}")
    if ctx.github_token is None:
        typer.echo("Note: no GitHub token found; running in local-git mode. PR, issue and "
                   f"review text are unavailable (set {ctx.cfg.github.token_env}).")


@app.command("why")
def why_cmd(
    target: Annotated[str, typer.Argument(help="FILE, FILE:LINE or FILE:START-END")],
    symbol: Annotated[str | None, typer.Option("--symbol", "-s",
                                               help="Function/class name.")] = None,
    rev: Annotated[str, typer.Option(help="Revision to explain.")] = "HEAD",
    newest: Annotated[bool, typer.Option("--newest", help="Newest first, not strongest.")] = False,
    repo: RepoOpt = Path("."), json_out: JsonOpt = False, quiet: QuietOpt = False,
    no_color: NoColorOpt = False, verbose: VerboseOpt = False,
    fmt: Annotated[str, typer.Option("--format", help="text | json | markdown")] = "text",
) -> None:
    """Why does this code exist? Evidence-backed, graded decision records."""
    if fmt not in ("text", "json", "markdown"):
        raise _fail(f"unknown --format {fmt!r}: use text, json or markdown")
    fmt = "json" if json_out else fmt
    ctx = _open(repo)
    _ensure_indexed(ctx, quiet or fmt != "text")
    path, start, end = parse_target(target)
    try:
        res = why(ctx, path, start, end, symbol=symbol, rev=rev)
    except (WhyError, GitError) as e:
        raise _fail(str(e)) from None
    if newest:
        res.decisions.sort(key=lambda d: (-d.introduced_at, d.id))
    if quiet:
        return
    typer.echo(dumps(why_json(res)) if fmt == "json" else why_markdown(res, ctx)
               if fmt == "markdown" else why_text(res, _out(ctx, no_color, verbose)))


@app.command("explain")
def explain_cmd(
    target: Annotated[str, typer.Argument(help="FILE:LINE")],
    rev: Annotated[str, typer.Option(help="Revision to explain.")] = "HEAD",
    repo: RepoOpt = Path("."), json_out: JsonOpt = False, no_color: NoColorOpt = False,
) -> None:
    """Show how every score for a line was derived: all factors and all evidence."""
    ctx = _open(repo)
    _ensure_indexed(ctx, json_out)
    path, start, end = parse_target(target)
    try:
        res = why(ctx, path, start, end, rev=rev)
    except (WhyError, GitError) as e:
        raise _fail(str(e)) from None
    if json_out:
        typer.echo(dumps(why_json(res)))
        return
    o = _out(ctx, no_color, True)
    typer.echo(why_text(res, o, full=True))
    typer.echo("\nScore = clamp(sum of factor points, 0, 100). Bands: "
               f"medium >= {ctx.cfg.scoring.bands.medium}, high >= {ctx.cfg.scoring.bands.high},"
               f" critical >= {ctx.cfg.scoring.bands.critical}. Confidence starts from the "
               "evidence grade and drops for shallow clones, missing index data and unfetched "
               "PRs.")


@app.command("check")
def check_cmd(
    base: Annotated[str | None, typer.Option(help="Base revision (default HEAD).")] = None,
    head: Annotated[str | None, typer.Option(help="Head revision; omit for the working tree.")]
    = None,
    staged: Annotated[bool, typer.Option("--staged", help="Check staged changes only.")] = False,
    pr: Annotated[bool, typer.Option(
        "--pr", help="Compare HEAD with the merge-base of --base (like a pull request).")] = False,
    diff_file: Annotated[Path | None, typer.Option(
        "--diff", help="Check a unified diff file ('-' for stdin) against --base.")] = None,
    hook: Annotated[bool, typer.Option(
        "--hook", help="Git-hook mode: fast, never auto-indexes, fails open on errors.")] = False,
    fmt: Annotated[str, typer.Option("--format", help="text | json | markdown")] = "text",
    output: Annotated[Path | None, typer.Option(
        "--output", "-o", help="Also write the report (in --format) to this file.")] = None,
    repo: RepoOpt = Path("."), json_out: JsonOpt = False, quiet: QuietOpt = False,
    no_color: NoColorOpt = False, verbose: VerboseOpt = False,
) -> None:
    """The fence check: is this change removing or weakening code that exists for a reason?"""
    from fence.render import check_markdown

    if json_out:
        fmt = "json"
    try:
        ctx = Context.open(repo, create=not hook)
    except (GitError, ConfigError, IndexUnavailable) as e:
        if hook:
            typer.echo(f"fence: skipped ({e})", err=True)
            raise typer.Exit(0) from None
        raise _fail(str(e), EXIT_NO_INDEX if isinstance(e, IndexUnavailable) else EXIT_ERROR) \
            from None
    if not hook:
        _ensure_indexed(ctx, quiet or fmt != "text")
    try:
        if diff_file is not None:
            text = sys.stdin.read() if str(diff_file) == "-" else diff_file.read_text("utf-8")
            res = check(ctx, diff_text=text, base=base or "HEAD", deep=not hook)
        else:
            b = base or "HEAD"
            h = head
            if pr:
                h = head or "HEAD"
                b = merge_base(ctx.repo, b, h)
            res = check(ctx, DiffSpec(base=b, head=h, staged=staged), deep=not hook)
    except (DiffError, GitError, OSError) as e:
        if hook:
            typer.echo(f"fence: skipped ({e})", err=True)
            raise typer.Exit(0) from None
        raise _fail(str(e)) from None
    report = (dumps(check_json(res)) if fmt == "json" else check_markdown(res, ctx)
              if fmt == "markdown" else check_text(res, _out(ctx, no_color, verbose)))
    if output:
        output.write_text(report + "\n", encoding="utf-8")
    if hook and res.exit_code == 0:
        raise typer.Exit(0)  # silent when nothing blocks
    if not quiet:
        typer.echo(report)
    if hook and res.exit_code:
        typer.echo("fence: commit blocked. Acknowledge with the `fence ack` command above, "
                   f"or bypass once with {ctx.cfg.policy.allow_bypass_env}=1.", err=True)
    raise typer.Exit(res.exit_code)


HOOK_MARK = "# installed by fence"


@app.command("hook")
def hook_cmd(
    action: Annotated[str, typer.Argument(help="install | uninstall")],
    pre_push: Annotated[bool, typer.Option("--pre-push", help="Also check on push.")] = False,
    force: Annotated[bool, typer.Option(help="Replace an existing non-fence hook.")] = False,
    repo: RepoOpt = Path("."),
) -> None:
    """Install or remove the fence git hooks (pre-commit, optionally pre-push)."""
    ctx = _open(repo)
    hooks_dir = Path(ctx.repo.run(["rev-parse", "--git-path", "hooks"]).strip())
    if not hooks_dir.is_absolute():
        hooks_dir = ctx.repo.root / hooks_dir
    scripts = {"pre-commit": "check --staged --hook"}
    if pre_push:
        scripts["pre-push"] = 'check --hook --pr --base "@{upstream}"'
    # Git runs hooks with its own sh and PATH. On Windows a venv's Scripts folder is often
    # not on it, and a hook that silently found no fence would check nothing. So record
    # the fence that installed the hook, fall back to PATH, and say so when neither exists.
    running = Path(sys.argv[0]).stem in ("fence", "gitfence")
    exe = (shutil.which(sys.argv[0]) if running else None) or shutil.which("fence") or ""
    exe = Path(exe).resolve().as_posix().replace("'", "'\\''") if exe else ""
    for name, cmd in scripts.items():
        target = hooks_dir / name
        existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        if action == "uninstall":
            if HOOK_MARK in existing:
                target.unlink()
                typer.echo(f"removed {target}")
            continue
        if action != "install":
            raise _fail("action must be install or uninstall")
        if existing and HOOK_MARK not in existing and not force:
            raise _fail(f"{target} exists and is not a fence hook; use --force to replace it")
        hooks_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"#!/bin/sh\n{HOOK_MARK}; `fence hook uninstall` removes it\n"
            f"FENCE='{exe}'\n"
            '[ -n "$FENCE" ] && [ -x "$FENCE" ] || FENCE=$(command -v fence) || {\n'
            '  echo "fence: not found, so this hook checked nothing;'
            ' re-run fence hook install" >&2\n'
            "  exit 0\n"
            "}\n"
            f'exec "$FENCE" {cmd}\n', encoding="utf-8", newline="\n")
        target.chmod(0o755)
        typer.echo(f"installed {target}")


@app.command("ack")
def ack_cmd(
    decision_id: Annotated[str, typer.Argument(help="Decision id, e.g. D3f9a1c2b7e")],
    at: Annotated[str, typer.Option("--at", help="FILE:LINE the decision covers.")],
    reason: Annotated[str, typer.Option("--reason", "-m", help="Why it is safe to proceed.")],
    rev: Annotated[str, typer.Option(help="Revision the line number refers to.")] = "HEAD",
    expires: Annotated[int | None, typer.Option(help="Days until it expires (default from "
                                                     "policy.ack_expiry_days).")] = None,
    repo: RepoOpt = Path("."), json_out: JsonOpt = False,
) -> None:
    """Record "I know why, proceed" in the team ledger (.fence/acks.jsonl)."""
    ctx = _open(repo)
    path, start, end = parse_target(at)
    if start is None:
        raise _fail("--at needs FILE:LINE")
    try:
        res = why(ctx, path, start, end, rev=rev, deep=False)
    except (WhyError, GitError) as e:
        raise _fail(str(e)) from None
    match = next((d for d in res.decisions if d.id == decision_id), None)
    if match is None:
        ids = ", ".join(d.id for d in res.decisions) or "none"
        raise _fail(f"{decision_id} does not cover {at} at {rev} (decisions there: {ids})")
    text = ctx.repo.read_blob(res.target.rev, res.target.path)
    fp = _decision_fingerprint(res.target.path, text.split("\n"), match)
    try:
        who = ctx.repo.run(["config", "user.email"]).strip() or "unknown"
    except GitError:
        who = "unknown"
    try:
        ack = ackmod.append(ctx.repo.root / ctx.cfg.policy.ack_ledger, decision_id=decision_id,
                            path=res.target.path, fingerprint=fp, reason=reason, who=who,
                            expiry_days=expires or ctx.cfg.policy.ack_expiry_days)
    except ackmod.AckError as e:
        raise _fail(str(e)) from None
    if json_out:
        typer.echo(dumps({"schema": "fence/v1", "kind": "ack", **ack.__dict__}))
    else:
        typer.echo(f"Acknowledged {decision_id} for {res.target.path}"
                   + (f" until {ack.expires[:10]}" if ack.expires else "")
                   + f". Commit {ctx.cfg.policy.ack_ledger} to share it.")


@app.command("doctor")
def doctor_cmd(repo: RepoOpt = Path("."), json_out: JsonOpt = False) -> None:
    """Diagnose the environment, index health and coverage."""
    checks: list[tuple[str, bool, str]] = []
    git = shutil.which("git")
    checks.append(("git", bool(git), git or "git not found on PATH"))
    try:
        ctx = Context.open(repo)
    except (GitError, ConfigError, IndexUnavailable) as e:
        checks.append(("repository", False, str(e)))
        _doctor_print(checks, json_out)
        raise typer.Exit(EXIT_ERROR) from None
    checks.append(("repository", True, str(ctx.repo.root)))
    checks.append(("config", True, ctx.cfg.source or "<defaults>"))
    n = ctx.store.commit_count()
    checks.append(("index", n > 0, f"{n} commits at {ctx.store.path}" if n else
                   "empty: run `fence index`"))
    checks.append(("index integrity", ctx.store.integrity_ok(), "PRAGMA quick_check"))
    stale = [name for name, sha in ctx.store.refs().items()
             if _safe_ref(ctx, name) not in (None, sha)]
    freshness = "up to date" if not stale else f"behind on {', '.join(stale)}: run `fence index`"
    checks.append(("index freshness", not stale, freshness))
    checks.append(("history depth", not ctx.shallow,
                   "full history" if not ctx.shallow else "shallow clone: older reasons invisible"))
    token = ctx.github_token
    gh = (f"token found in {ctx.cfg.github.token_env}" if token
          else f"no token ({ctx.cfg.github.token_env}): local-git mode")
    gh += f"; remote {ctx.slug[0]}/{ctx.slug[1]}" if ctx.slug else "; no GitHub remote"
    checks.append(("github", True, gh))
    _, problems = ackmod.load(ctx.repo.root / ctx.cfg.policy.ack_ledger)
    checks.append(("ack ledger", not problems, "; ".join(problems) or "ok"))
    from fence.lang import supported_languages

    checks.append(("languages", True, ", ".join(supported_languages())))
    _doctor_print(checks, json_out)
    if not all(ok for name, ok, _ in checks if name in ("git", "index integrity")):
        raise typer.Exit(EXIT_ERROR)


def _safe_ref(ctx: Context, name: str) -> str | None:
    try:
        return ctx.repo.verify_ref(name)
    except GitError:
        return None


def _doctor_print(checks: list[tuple[str, bool, str]], json_out: bool) -> None:
    if json_out:
        typer.echo(dumps({"schema": "fence/v1", "kind": "doctor",
                          "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks]}))
        return
    for name, ok, detail in checks:
        typer.echo(f"{'ok  ' if ok else 'WARN'}  {name:<16} {detail}")


@app.command("config")
def config_cmd(repo: RepoOpt = Path("."), json_out: JsonOpt = False) -> None:
    """Show the effective configuration (file values over defaults) and where it came from."""
    try:
        root = Context.open(repo).repo.root
        cfg = load_config(root)
    except (ConfigError, GitError, IndexUnavailable) as e:
        raise _fail(str(e)) from None
    data = cfg.model_dump(mode="json")
    if json_out:
        typer.echo(dumps(data))
        return
    typer.echo(f"# source: {data.pop('source')}")
    typer.echo(f"# index: {db_path_for(cfg.cache_dir, root)}")
    for section, values in data.items():
        typer.echo(f"\n[{section}]")
        for k, v in values.items():
            typer.echo(f"{k} = {dumps(v) if not isinstance(v, str) else repr(v)}")


@app.command("mcp")
def mcp_cmd(repo: RepoOpt = Path(".")) -> None:
    """Run the MCP server on stdio (for Claude Code and other agents)."""
    from fence.mcp.server import serve

    serve(str(repo))


@app.command("scan")
def scan_cmd(
    path: Annotated[str, typer.Argument(help="Directory (default: repo root).")] = ".",
    limit: Annotated[int, typer.Option(help="Max decisions to show.")] = 20,
    repo: RepoOpt = Path("."), json_out: JsonOpt = False, no_color: NoColorOpt = False,
) -> None:
    """List everything in a directory you should NOT simply delete, strongest first."""
    from fence.scan import scan

    ctx = _open(repo)
    _ensure_indexed(ctx, json_out)
    try:
        res = scan(ctx, path)
    except GitError as e:
        raise _fail(str(e)) from None
    if json_out:
        typer.echo(dumps({"schema": "fence/v1", "kind": "scan", "path": res.path,
                          "files_scanned": res.files_scanned, "notes": res.notes,
                          "decisions": [{"file": f, **d.to_dict()} for f, d in
                                        res.decisions[:limit]]}))
        return
    o = _out(ctx, no_color, False)
    typer.echo(f"{res.path}: {len(res.decisions)} decisions in {res.files_scanned} files")
    for f, d in res.decisions[:limit]:
        s = d.strength
        lines = ",".join(f"{a.start_line}-{a.end_line}" for a in d.anchors[:3])
        typer.echo(f"{o.c(f'[{s.band.upper()} {s.value}]', s.band)} {f}:{lines} | {d.type} | "
                   f"grade {d.grade} | {d.reason_text[:100]}")
    for n in res.notes:
        typer.echo(f"Note: {n}")


if __name__ == "__main__":
    app()


@app.command("backtest")
def backtest_cmd(
    max_commits: Annotated[int, typer.Option(help="Commits to replay.")] = 300,
    window: Annotated[int, typer.Option(help="Later commits in which a regret can appear.")]
    = 200,
    rev: Annotated[str, typer.Option(help="Newest revision of the replayed range.")] = "HEAD",
    fast: Annotated[bool, typer.Option("--fast", help="Skip line-history walks.")] = False,
    dump: Annotated[Path | None, typer.Option(help="Write every block outcome as JSONL.")]
    = None,
    repo: RepoOpt = Path("."), json_out: JsonOpt = False,
) -> None:
    """Removal-Regret Backtest: would Fence have warned about removals later undone?"""
    from fence.eval.backtest import backtest

    ctx = _open(repo)
    if not json_out:
        typer.echo("fence: indexing...", err=True)
    index_repo(ctx.repo, ctx.store, ctx.cfg, detect_format=detect_format_only)

    def progress(n: int, total: int) -> None:
        if not json_out:
            typer.echo(f"  replayed {n}/{total} commits...", err=True)

    try:
        rep = backtest(ctx, rev=rev, max_commits=max_commits, window=window, deep=not fast,
                       progress=progress)
    except GitError as e:
        raise _fail(str(e)) from None
    s = rep.summary()
    if dump:
        import json

        dump.write_text("".join(json.dumps(o.__dict__) + "\n" for o in rep.outcomes),
                        encoding="utf-8")
    if json_out:
        examples = [o.__dict__ for o in rep.outcomes if o.regretted][:20]
        typer.echo(dumps({"schema": "fence/v1", "kind": "backtest", **s, "regretted_examples":
                          examples}))
        return
    for k, v in s.items():
        if k != "calibration":
            typer.echo(f"{k:<32} {v}")
    typer.echo("calibration (all blocks per shipped risk bin):")
    for b in s["calibration"]:
        typer.echo(f"  risk {b['risk']}: table {b['table_rate']}, observed {b['observed_rate']} "
                   f"({b['regretted']}/{b['blocks']})")
    typer.echo("Note: regret labels are a lower bound, so precision is conservative.")


@app.command("comment")
def comment_cmd(
    report: Annotated[Path, typer.Option(help="Markdown from `fence check --format markdown`.")],
    pr: Annotated[int, typer.Option(help="Pull request number.")],
    repo_slug: Annotated[str | None, typer.Option(
        "--repo-slug", help="owner/name (default: $GITHUB_REPOSITORY).")] = None,
) -> None:
    """Post or update the single sticky Fence comment on a PR (for CI)."""
    import os

    from fence.github.comment import upsert

    token = os.environ.get("GITHUB_TOKEN")
    slug = repo_slug or os.environ.get("GITHUB_REPOSITORY")
    if not token or not slug:
        raise _fail("GITHUB_TOKEN and GITHUB_REPOSITORY (or --repo-slug) are required")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    result = upsert(api, slug, pr, report.read_text(encoding="utf-8"), token)
    if result == "forbidden":
        typer.echo("fence: token cannot comment (fork PR?); see the job summary instead",
                   err=True)
    else:
        typer.echo(f"fence: PR comment {result}")


@app.command("report")
def report_cmd(
    path: Annotated[str, typer.Argument(help="Directory (default: repo root).")] = ".",
    fmt: Annotated[str, typer.Option("--format", help="markdown | json | html")] = "markdown",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    repo: RepoOpt = Path("."),
) -> None:
    """Hotspot report: load-bearing, fragile-undocumented, churn and ownership."""
    from fence import report

    ctx = _open(repo)
    _ensure_indexed(ctx, True)
    try:
        data = report.build(ctx, path)
    except GitError as e:
        raise _fail(str(e)) from None
    text = (dumps(data) if fmt == "json" else report.to_html(data) if fmt == "html"
            else report.to_markdown(data))
    if output:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}")
    else:
        typer.echo(text)

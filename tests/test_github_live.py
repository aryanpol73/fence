"""Live GitHub check for grade A. Skipped unless explicitly enabled; never part of the
normal suite, which needs no network.

    FENCE_LIVE_GITHUB=1 GITHUB_TOKEN=<read-only token> uv run pytest tests/test_github_live.py

It clones a public repository (psf/requests by default, override with
FENCE_LIVE_REPO=owner/name) without blobs over 1 MB, syncs at most 40 merged PRs, then looks for
one decision whose stated reason is quoted from PR or issue text. About 100 API
requests. Until this test has actually run and passed, live GitHub behaviour is
covered only by the recorded-response tests in tests/test_github.py.
"""

import os
import subprocess
from pathlib import Path

import pytest

from fence.context import Context
from fence.decisions.build import WhyError, why
from fence.github.sync import sync
from fence.index.indexer import index_repo

pytestmark = pytest.mark.skipif(
    os.environ.get("FENCE_LIVE_GITHUB") != "1" or not os.environ.get("GITHUB_TOKEN"),
    reason="live GitHub test: set FENCE_LIVE_GITHUB=1 and GITHUB_TOKEN to run",
)


def test_live_sync_reaches_grade_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    slug = os.environ.get("FENCE_LIVE_REPO", "psf/requests")
    clone = tmp_path / "repo"
    # Blobs over 1 MB are left out, as in evals/run_suite.py. Not blob:none: indexing runs
    # `git log --numstat`, which needs every changed blob, so a blob-less clone fetches
    # them one commit at a time during indexing (about 50 minutes for requests, and any
    # network drop fails it with git exit 128).
    subprocess.run(["git", "clone", "-q", "--filter=blob:limit=1m",
                    f"https://github.com/{slug}.git", str(clone)], check=True, timeout=600)
    ctx = Context.open(clone)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    rep = sync(ctx, max_prs=40)
    assert rep.stopped is None, rep.stopped
    assert rep.prs > 0, "no merged PRs mapped from commit subjects"

    rows = ctx.store.conn.execute(
        "SELECT DISTINCT f.path FROM file_changes f JOIN commits c ON c.sha = f.sha "
        "JOIN prs p ON p.number = c.pr_number WHERE p.body <> '' LIMIT 60").fetchall()
    seen_pr_evidence = False
    for (path,) in rows:
        try:
            decisions = why(ctx, path).decisions
        except WhyError:
            continue  # deleted since, binary or empty
        for d in decisions:
            seen_pr_evidence |= any(e.type == "pr" and e.retrieval_status == "ok"
                                    for e in d.evidence)
            if d.grade == "A":
                src = next(e for e in d.evidence if e.id == d.stated_reason_evidence)
                assert src.type in ("pr", "issue", "review_comment")
                assert d.stated_reason and src.quote and src.url
                return
    assert seen_pr_evidence, "PR text was never attached as evidence"
    pytest.fail("PR evidence was attached, but no decision reached grade A in the sample")

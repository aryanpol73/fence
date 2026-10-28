"""Everything one Fence operation needs: repo, config, index, and what we cannot see."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from fence.config import Config, load_config
from fence.git.runner import GitError, Repo
from fence.index.store import Store, db_path_for

_GITHUB = re.compile(r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
                     r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def github_slug(remote_url: str) -> tuple[str, str] | None:
    m = _GITHUB.match(remote_url.strip())
    return (m.group(1), m.group(2)) if m else None


@dataclass
class Context:
    repo: Repo
    cfg: Config
    store: Store
    slug: tuple[str, str] | None = None
    notes: list[str] = field(default_factory=list)
    # Backtests set this to the commit time being replayed, so no later history leaks in.
    as_of: int | None = None

    @classmethod
    def open(cls, path: str | Path = ".", *, create: bool = True) -> Context:
        repo = Repo.open(path)
        cfg = load_config(repo.root)
        repo = Repo(repo.root, timeout=cfg.performance.git_timeout_seconds)
        store = Store.open(db_path_for(cfg.cache_dir, repo.root), create=create)
        slug = None
        try:
            slug = github_slug(repo.run(["remote", "get-url", "origin"]))
        except GitError:
            pass
        return cls(repo, cfg, store, slug)

    @property
    def web_base(self) -> str | None:
        return f"https://github.com/{self.slug[0]}/{self.slug[1]}" if self.slug else None

    @property
    def github_token(self) -> str | None:
        if self.cfg.github.enabled == "off":
            return None
        return os.environ.get(self.cfg.github.token_env) or None

    @property
    def offline(self) -> bool:
        """True when PR/issue text cannot be read (no token, no GitHub remote, or disabled)."""
        has_prs = self.store.conn.execute("SELECT 1 FROM prs LIMIT 1").fetchone() is not None
        return not has_prs

    @property
    def shallow(self) -> bool:
        return self.store.get_meta("shallow") == "1"

    def commit_url(self, sha: str) -> str | None:
        return f"{self.web_base}/commit/{sha}" if self.web_base else None

    def pr_url(self, n: int) -> str | None:
        return f"{self.web_base}/pull/{n}" if self.web_base else None

    def issue_url(self, n: int) -> str | None:
        return f"{self.web_base}/issues/{n}" if self.web_base else None

    def close(self) -> None:
        self.store.close()

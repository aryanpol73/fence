"""Build real git fixture repos for tests. Dates advance one day per commit, so SHAs
are reproducible and ordering is unambiguous."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

BASE_TIME = 1_767_225_600  # 2026-01-01T00:00:00Z


class FixtureRepo:
    def __init__(self, root: Path, *, branch: str = "main", init: bool = True) -> None:
        self.root = root
        self.tick = 0
        if init:
            root.mkdir(parents=True, exist_ok=True)
            self.git("init", "-q", "-b", branch)

    def env(self, who: str = "Dev One", email: str = "dev1@example.com") -> dict[str, str]:
        date = f"{BASE_TIME + self.tick * 86_400} +0000"
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": who,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": who,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }

    def git(self, *args: str, who: str = "Dev One", email: str = "dev1@example.com") -> str:
        proc = subprocess.run(
            ["git", "-c", "core.autocrlf=false", *args], cwd=self.root, env=self.env(who, email),
            capture_output=True, text=True, timeout=60, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
        return proc.stdout.strip()

    def write(self, files: dict[str, str | bytes | None]) -> None:
        for rel, content in files.items():
            target = self.root / rel
            if content is None:
                self.git("rm", "-q", "--", rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_bytes(content.encode())  # no newline translation
            self.git("add", "--", rel)

    def commit(
        self, files: dict[str, str | bytes | None], message: str, *, who: str = "Dev One",
        email: str = "dev1@example.com",
    ) -> str:
        self.tick += 1
        self.write(files)
        self.git("commit", "-q", "--allow-empty", "-m", message, who=who, email=email)
        return self.head()

    def mv(self, old: str, new: str) -> None:
        (self.root / new).parent.mkdir(parents=True, exist_ok=True)
        self.git("mv", old, new)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def merge(self, branch: str, message: str) -> str:
        self.tick += 1
        self.git("merge", "-q", "--no-ff", "-m", message, branch)
        return self.head()

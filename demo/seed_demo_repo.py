"""Build the local fence-demo git history used by tests and the demo.

The story: a payments client retries timed-out charges. Retrying immediately
caused duplicate charges, so a 2-second wait was added. Months later a
"cleanup" removes the wait. The reason for the wait is NOT in a code comment;
it lives only in history, which is the situation Fence exists for.

History on `main` (dates and authors are fixed so SHAs are reproducible):
  C1  add payments client with retry          (2-space indent)
  C2  the fence: wait 2s before retrying      (adds `import time`, `time.sleep(2)`)
  C3  formatting-only reformat                (2 -> 4 spaces, single -> double quotes)
  C4  list C3 in .git-blame-ignore-revs       (optional, see --no-ignore-revs)
  C5  add refund()                            (unrelated pure addition)
Branches off main:
  cleanup-payments  removes the sleep and the now-unused import  -> cleanup.diff
  harmless-rename   renames a parameter in refund()               -> harmless.diff

Usage: python demo/seed_demo_repo.py [--out demo/out] [--no-ignore-revs] [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

MARKER = ".fence-demo-seed"  # only directories carrying this marker may be replaced
PATH = "payments/client.py"

C1 = '''\
"""Client for the Acme payments gateway."""
import logging

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class GatewayTimeout(Exception):
  pass


def charge(gateway, card_token, amount_cents, idempotency_key):
  for attempt in range(MAX_ATTEMPTS):
    try:
      return gateway.create_charge(card_token, amount_cents, idempotency_key=idempotency_key)
    except GatewayTimeout:
      log.warning('charge timed out, attempt %d', attempt + 1)
      if attempt == MAX_ATTEMPTS - 1:
        raise
'''

C2 = C1.replace("import logging\n", "import logging\nimport time\n").replace(
    "        raise\n", "        raise\n      time.sleep(2)\n"
)


def _reformat(src: str) -> str:
    """What the formatter did: double every leading indent, normalise quotes."""
    out = []
    for line in src.splitlines(keepends=True):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        out.append(" " * (indent * 2) + stripped)
    return "".join(out).replace("'charge timed out, attempt %d'", '"charge timed out, attempt %d"')


C3 = _reformat(C2)

C5 = C3 + '''

def refund(gateway, charge_id, amount_cents):
    return gateway.create_refund(charge_id, amount_cents)
'''

CLEANUP = C5.replace("import time\n", "").replace("            time.sleep(2)\n", "")
HARMLESS = C5.replace(
    "def refund(gateway, charge_id, amount_cents):\n"
    "    return gateway.create_refund(charge_id, amount_cents)",
    "def refund(gateway, charge_id, amount):\n    return gateway.create_refund(charge_id, amount)",
)

PEOPLE = {
    "priya": ("Priya Nair", "priya@example.com"),
    "rahul": ("Rahul Mehta", "rahul@example.com"),
    "sam": ("Sam Okafor", "sam@example.com"),
    "alex": ("Alex Kim", "alex@example.com"),
}


def _git(
    repo: Path, *args: str, who: str = "priya", date: str = "2026-01-01T10:00:00+05:30"
) -> str:
    name, email = PEOPLE[who]
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
        "GIT_CONFIG_GLOBAL": os.devnull,  # the user's global config must not change SHAs
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    proc = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, timeout=30, check=True
    )
    return proc.stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str, who: str, date: str) -> str:
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, newline="\n")  # same bytes, so same SHAs, on every OS
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message, who=who, date=date)
    return _git(repo, "rev-parse", "HEAD")


def seed(out: Path, *, ignore_revs: bool = True, force: bool = False) -> dict[str, str]:
    """Create the demo repo under `out` and return the interesting SHAs and paths."""
    out = out.resolve()
    repo = out / "fence-demo"
    if out.exists() and any(out.iterdir()):
        if not (out / MARKER).exists():
            raise SystemExit(f"refusing to touch {out}: not created by this script")
        if not force:
            raise SystemExit(f"{out} already exists; pass --force to rebuild it")
        shutil.rmtree(out)
    repo.mkdir(parents=True)
    (out / MARKER).write_text("created by demo/seed_demo_repo.py\n")

    _git(repo, "init", "-q", "-b", "main")
    shas: dict[str, str] = {}
    shas["initial"] = _commit(
        repo, {PATH: C1}, "Add Acme payments client with retry on timeout", "priya",
        "2026-03-02T11:00:00+05:30",
    )
    shas["fence"] = _commit(
        repo, {PATH: C2},
        "Wait 2s before retrying a timed-out charge (#2)\n\n"
        "Retrying immediately after a gateway timeout produced duplicate charges.\n"
        "Fixes #1.",
        "rahul", "2026-04-14T16:30:00+05:30",
    )
    shas["format"] = _commit(
        repo, {PATH: C3}, "Reformat payments with black (#3)", "sam", "2026-05-20T09:15:00+05:30"
    )
    if ignore_revs:
        shas["ignore_revs"] = _commit(
            repo,
            {".git-blame-ignore-revs": f"# Reformat payments with black (#3)\n{shas['format']}\n"},
            "Hide the black reformat from git blame (#4)", "sam", "2026-05-20T09:30:00+05:30",
        )
    shas["refund"] = _commit(
        repo, {PATH: C5}, "Add refund() to payments client (#5)", "priya",
        "2026-07-08T14:00:00+05:30",
    )

    _git(repo, "checkout", "-q", "-b", "cleanup-payments")
    shas["cleanup"] = _commit(
        repo, {PATH: CLEANUP}, "Tidy charge retry loop", "alex", "2026-10-20T12:00:00+05:30"
    )
    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "harmless-rename")
    shas["harmless"] = _commit(
        repo, {PATH: HARMLESS}, "Rename refund amount parameter", "alex",
        "2026-10-21T12:00:00+05:30",
    )
    _git(repo, "checkout", "-q", "main")

    (out / "cleanup.diff").write_text(_git(repo, "diff", "main", "cleanup-payments") + "\n")
    (out / "harmless.diff").write_text(_git(repo, "diff", "main", "harmless-rename") + "\n")

    result = {**shas, "repo": str(repo), "base": "main", "path": PATH}
    (out / "shas.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    parser.add_argument("--no-ignore-revs", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = seed(args.out, ignore_revs=not args.no_ignore_revs, force=args.force)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

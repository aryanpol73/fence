"""Obsolescence (F7/D3): concrete evidence that a fence may no longer be needed.

Only concrete, checkable facts mark a decision stale-suspected:
- a dependency its commit names was removed from the manifests, or upgraded;
- a feature flag its lines read no longer appears anywhere else in the repo;
- the function it lives in has no remaining references.
The output is always "possibly obsolete, verify", never an instruction.
"""

from __future__ import annotations

import json
import re
import tomllib

from fence.git.runner import GitError, Repo

MANIFESTS = ("requirements.txt", "pyproject.toml", "package.json", "go.mod", "setup.py")
_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:[=<>~!]=?\s*([\w.]+))?")
_GO = re.compile(r"^\s*(?:require\s+)?([\w./-]+\.[\w./-]+)\s+v([\w.+-]+)", re.M)
_FLAG = re.compile(r"(?:flags?\.(\w+)|is_?enabled\(\s*['\"]([\w.-]+)['\"])", re.I)


def _version_key(v: str | None) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.+-]", v or "")[:4])


def manifests(repo: Repo, rev: str) -> dict[str, str | None]:
    """{package: version or None} from the manifests at `rev` (root directory only)."""
    deps: dict[str, str | None] = {}
    for name in MANIFESTS:
        try:
            text = repo.read_blob(rev, name, max_bytes=500_000)
        except GitError:
            continue
        if name == "package.json":
            try:
                data = json.loads(text)
            except ValueError:
                continue
            for sec in ("dependencies", "devDependencies", "peerDependencies"):
                for k, v in (data.get(sec) or {}).items():
                    deps[k.lower()] = re.sub(r"^[^\d]*", "", str(v)) or None
        elif name == "go.mod":
            for m in _GO.finditer(text):
                deps[m.group(1).lower()] = m.group(2)
        elif name == "pyproject.toml":
            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                continue
            reqs = list((data.get("project") or {}).get("dependencies") or [])
            for group in ((data.get("project") or {}).get("optional-dependencies") or {}).values():
                reqs += list(group)
            for r in reqs:
                if m := _REQ.match(str(r)):
                    deps[m.group(1).lower()] = m.group(2)
        else:
            for line in text.splitlines():
                if line.strip().startswith(("#", "-")):
                    continue
                if m := _REQ.match(line):
                    deps[m.group(1).lower()] = m.group(2)
    return deps


def dependency_findings(repo: Repo, intro_sha: str, rev: str, message: str,
                        cache: dict) -> list[str]:
    then = cache.setdefault(("m", intro_sha), manifests(repo, intro_sha))
    now = cache.setdefault(("m", rev), manifests(repo, rev))
    out = []
    text = message.lower()
    for pkg, v_then in sorted(then.items()):
        if len(pkg) < 3 or not re.search(rf"(?<![\w-]){re.escape(pkg)}(?![\w-])", text):
            continue
        if pkg not in now:
            out.append(f"dependency '{pkg}' named in the commit has since been removed")
        elif v_then and now[pkg] and _version_key(now[pkg]) > _version_key(v_then):
            out.append(f"dependency '{pkg}' named in the commit was upgraded "
                       f"from {v_then} to {now[pkg]}")
    return out


def flag_findings(repo: Repo, rev: str, lines: list[str], path: str) -> list[str]:
    out = []
    for line in lines:
        for m in _FLAG.finditer(line):
            flag = m.group(1) or m.group(2)
            if not flag or len(flag) < 4:
                continue
            hits = _grep_count(repo, rev, flag)
            if hits is not None and hits <= 1:
                out.append(f"feature flag '{flag}' is not referenced anywhere else")
    return out


def caller_findings(repo: Repo, rev: str, symbol: str | None, path: str = "") -> list[str]:
    if not symbol or symbol == "<module>":
        return []
    name = symbol.rsplit(".", 1)[-1]
    if len(name) < 4 or name.startswith("__") or name in ("main", "init", "setup", "test"):
        return []
    # Only private symbols: a public function may be called by code outside this repo.
    private = (path.endswith((".py", ".pyi")) and name.startswith("_")) or (
        path.endswith(".go") and name[0].islower())
    if not private:
        return []
    hits = _grep_count(repo, rev, name)
    if hits is not None and hits <= 1:
        return [f"'{symbol}' has no remaining references in the repository"]
    return []


def _grep_count(repo: Repo, rev: str, word: str) -> int | None:
    if not re.fullmatch(r"[\w.-]+", word):
        return None
    try:
        out = repo.run(["grep", "-c", "-w", "-F", "-e", word, rev, "--"])
    except GitError:
        return 0  # git grep exits 1 when nothing matches
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if ":" in line)

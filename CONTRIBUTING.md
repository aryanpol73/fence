# Contributing to Fence

Thanks for helping. Fence is small on purpose: deterministic, local-first, with no LLM
in the core. Read `CLAUDE.md` (the rules every change follows), `ARCHITECTURE.md` (how
the pieces fit) and `DECISIONS.md` (why defaults are what they are) before larger
changes.

## Setup

```bash
git clone https://github.com/aryanpol73/fence && cd fence
uv sync
uv run --python 3.12 pytest -q
uv run ruff check
```

Python 3.12+ and git are required. Windows, macOS and Linux are all supported and
tested in CI. On Windows without Developer Mode, two symlink tests are skipped.

## Rules for changes

- **Git only through `fence/git/runner.py`.** List-form arguments, no shell, a timeout on
  every call, refs and paths validated. No `subprocess` anywhere else in `fence/`.
- **Repository text is untrusted.** Commit messages, PRs, issues and code are quoted as
  evidence, never executed or followed.
- **Grounded or labelled.** A stated reason is a verbatim quote or nothing. Anything
  inferred starts with `Inferred:`. No reason means grade D, and grade D never blocks.
- **Scores are experimental.** Do not add wording that presents Strength or Change Risk as
  a probability or as predictive. Tuning weights or thresholds needs backtest evidence,
  recorded in `DECISIONS.md`.
- **Tests on real git fixtures.** Use `tests/gitfix.py`; do not mock git. Every fix gets a
  regression test that fails without it.
- **No network in the normal suite.** GitHub behaviour is tested with recorded responses
  (`tests/test_github.py`). The live check (`tests/test_github_live.py`) runs only with
  `FENCE_LIVE_GITHUB=1` and `GITHUB_TOKEN`.
- **Commit messages explain why**, not just what. Keep each commit buildable on its own.

## Adding a language

Implement the `LanguagePlugin` protocol in `fence/lang/__init__.py` (`name`,
`extensions`, `symbols` and `tokens`), register it in `_ensure_loaded` with `register()`,
and add cases to `tests/test_lang.py`. Files with
no plugin still work, with line anchors and whitespace-insensitive tokens.

## Evaluations

The backtest suite is slow (about 1.5 hours for 11 repos) and not part of CI:

```bash
uv run python evals/run_suite.py --only requests
uv run python evals/analyze.py
```

`evals/reason_markers.py` compares reason detection between two versions of
`fence/decisions/reasons.py` over the indexed commit messages, offline, in seconds. See
`evals/results/requests-before-after/README.md` for a reproducible before/after run.

## Reporting issues

Include `fence doctor --json`, the command you ran and its output. Do not paste private
repository content you cannot share.

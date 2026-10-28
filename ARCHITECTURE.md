# Fence architecture

Choices and the reason for each. Changes to brief defaults go in DECISIONS.md.

## Landscape (checked 2026-10-28)

| Tool class | What it gives | What it misses |
|---|---|---|
| git blame, GitLens, blame UIs | who and when | why; misled by refactors and squash merges unless tuned |
| ADRs, design docs | why, when someone writes it | manual, rare, drift from code |
| CodeScene, churn and hotspot tools | where risk is | the reason behind it |
| Code search, AI assistants | code retrieval | intent; they make up reasons |
| CODEOWNERS, review bots | routing to people | the constraint itself |
| **Correction:** "why" MCP servers (git-context-mcp, WhyGraph, an arXiv prototype) | blame, then PR, then issue context for an agent, often summarised by an LLM | no change-time check, no evidence grades, no measured accuracy, no ack ledger |

So the wedge is narrower than the brief says, but it holds. Fence checks a diff before it lands, grades its evidence strictly, says "reason unknown" when it does not know, and publishes backtested precision and recall.

## Stack

- **Python 3.12, packaged with uv/hatchling.** The Day 1 code is already Python. git does the heavy lifting, so Python costs nothing in speed. `uv tool install` gives a one-command install.
- **Dependencies, kept small:**
  - typer: CLI.
  - pydantic: config validation and versioned JSON schemas.
  - unidiff: diff parsing.
  - tree-sitter plus one grammar wheel each for JavaScript, TypeScript/TSX, Go and Java (prebuilt, no compiler). Python uses the stdlib `ast`/`tokenize`.
- **The standard library covers the rest:** sqlite3, tomllib, urllib (GitHub), json, hashlib, concurrent.futures.
- **No MCP SDK.** Its dependency tree (web server, async stack, HTTP client) is heavier than the protocol Fence needs: JSON-RPC 2.0 over stdio with `initialize`, `tools/list` and `tools/call`. A hand-written server of about 300 lines is tested against the protocol directly.
- **No LLM in the core.** The optional synthesis pass (P2) sits behind an interface, is off by default, and has a cost cap.

## Layout (`fence/`)

```
git/        runner.py (the ONLY subprocess caller), blame, log, stream (bounded log streaming)
lang/       plugin registry: stdlib plugin for Python, tree-sitter plugins for JS/TS/Go/Java: symbols, normalized tokens
anchors/    Anchor model, fingerprint (normalized-token hash + 5-token shingles), re-find
index/      SQLite store, migrations, incremental indexer, rename graph, PR mapping
github/     REST client (ETag cache, rate limits, pagination, resumable cursor), offline report
signals/    signal catalog: one class per signal, each with id, weight and unit test
decisions/  Decision Record builder, grading (A-D), status (active/superseded/reverted/...)
scoring/    Fence Strength, Change Risk, factor breakdowns, bands, "what would change this"
check/      diff -> hunks -> change kind (delete/loosen/alter/move) -> trace -> verdict
policy/     fence.toml model + validation, ack ledger
eval/       fixture builders, backtest (D1), metrics, calibration
mcp/        stdio JSON-RPC server, tool schemas, guardrail mode
render/     human output (text labels, never colour alone) and JSON (schema v1)
cli.py      typer entry points; stable exit codes
```

## Data flow

1. **`index`**
   - Streams `git log` (`--raw -M -C --numstat`, NUL-delimited) in batches of 500 commits. Each batch is one SQLite transaction, which gives a resume checkpoint, crash safety and bounded memory.
   - Stores commits, parents, trailers, file changes with rename similarity, and text signals per commit.
   - Maps PRs. Without the API, it reads `Merge pull request #N` and squash `(#N)` subjects. With a token, it uses `commits/{sha}/pulls`.
   - Marks formatting-only commits: their diffs are equal after token normalisation. Those commits join `.git-blame-ignore-revs`, which is read from the base revision only.
   - On each run, compares stored ref tips with current ones. SHAs no longer reachable are marked orphaned, which covers force-pushes and rebases.
2. **`why`**
   - Blames the target (`-w -M -C -C`, with the ignore list) at an explicit revision.
   - Groups the result into Decision Records: introducing commit × file × enclosing symbol.
   - Pulls in evidence from the index.
   - Walks `git log -L` for later touches, reverts and re-fixes, which form the regression chain.
   - Scores and renders the result.
   - Caches results per (revision, path, range) in the index DB.
3. **`check`**
   - Parses the diff (working tree, staged, range or PR) and checks every removed range against the base pre-image. Day 1 already does this.
   - Classifies each hunk:
     - delete: lines gone;
     - loosen: a guard keyword or comparison removed or weakened, or a retry, timeout or lock changed;
     - alter: constants or expressions changed;
     - move: the content's fingerprint reappears elsewhere in the same diff.
   - Traces each hunk to its decisions, scores the risk, applies policy and acks, and returns a verdict.

## Core models

- **Anchor:** `{path, line range | symbol qualname | file, language, fingerprint, path_history}`.
- **Fingerprint:**
  - sha256 of the symbol's token stream, with whitespace and comments dropped and identifiers and literals kept;
  - plus a set of 5-token shingles.
  - "Same code" means a Jaccard similarity of 0.7 or more (tunable). Anchors are re-found after moves and extract-method this way.
- **Evidence:** typed and citable (SHA or URL, timestamp, author, `retrieval_status`). Text is stored verbatim and treated as untrusted. It is never interpreted as instructions, and it is cleaned with `fence/text.py` before display.
- **Grades, strict:**
  - **A:** an explicit reason in a PR, issue or review that links to the change.
  - **B:** an explicit reason in a commit message or code comment.
  - **C:** inferred only from shape, labels or revert/fix patterns.
  - **D:** "reason unknown".
  - `stated_reason` is always a verbatim quote or null. `inferred_reason` is labelled as inferred.

## Scoring

- **Fence Strength** is a sum of named factors. Each factor returns `(id, points, plain-English sentence)`. The total is clamped to 0-100 and rounded.
- Weights live in `WEIGHTS` in `fence/scoring/strength.py`, can be overridden under `[scoring.weights]` in `fence.toml`, and are tuned with the backtest (DECISIONS.md).
- **Change Risk** = Strength × a change-kind multiplier (delete 1.0 > loosen 0.85 > alter 0.5 > move 0.3), plus bonuses for removed tests and sensitive paths. VERIFY needs risk >= 25 for stated grades A/B and >= 50 for inferred C/D.
- **Confidence** comes from grade, evidence coverage (offline mode or shallow clone lowers it) and the number of corroborating signals, then is calibrated against backtest outcomes.
- The same inputs always produce the same output: ordering is stable everywhere.
- `fence explain` prints every factor.

## Policy and acks

- `fence.toml` holds every option, and every option has a default. Zero config works.
- The ack ledger is `.fence/acks.jsonl`. It is committed and append-only. Each entry holds who, when, reason, scope (`decision_id` + anchor fingerprint) and an optional expiry.
- An ack is void when its fingerprint or decision no longer matches. That stops old acks from going stale silently.
- **Exit codes:**
  - 0: ok
  - 1: error
  - 2: policy threshold exceeded
  - 3: acknowledgement required
  - 4: index unavailable
- The GitHub Action treats 1 and 4 as a pass unless `fail_on_error` is set. This keeps Day 1's fail-open rule.

## Interfaces

- **CLI:** `index`, `why`, `check`, `ack`, `list`, `scan`, `report`, `explain`, `doctor`, `config`, `export`, `backtest`, `hook install`, `mcp`. Every command takes `--json`, `--quiet`, `--no-color` and `--verbose`.
- **MCP tools:**
  - `why`, `check`, `list_fences` and, when write tools are enabled, `acknowledge`.
  - Responses are capped at about 1.5k tokens by default, with a `detail` parameter to expand them.
  - Guardrail mode: above the configured threshold, the response begins with `STOP: ask the human before editing`, followed by the evidence.
- **Git hooks:** run only on a warm index, with a 2s budget, and fail open.
- **GitHub Action:**
  - Runs `fence check --format github`, then upserts one sticky comment, found by a hidden marker.
  - Runs from forks on `pull_request` with a read-only token. It never runs on `pull_request_target`.
- **Web view:** P2, and only if everything above is solid.

## Security

- Local-first, with no telemetry.
- Every git call goes through `runner.py`: list arguments, validated refs and paths, timeouts, no pager or prompts, read-only.
- Repo text is never executed and never followed as instructions. Size caps apply to blobs, diffs, ranges and API pages.
- Tokens are read only from the environment, redacted in errors and never stored.
- The ignore-revs list is read from the base revision, so a PR cannot hide history.

## Evaluation (`fence backtest`)

- **Method:** replay history. For each commit C that removes or weakens lines, ask whether those lines (fingerprint match) were restored, re-fixed or reverted within N commits or days (default 200/180). If yes, the removal was **regretted**. Fence then scores C using only data available at C's parent.
- **Metrics:**
  - precision = warned ∩ regretted / warned
  - recall = warned ∩ regretted / regretted
  - noise = warnings per 1k commits
  - median `check` latency
- Plus attribution accuracy on a labelled fixture set, compared with plain `blame` and `--follow`.
- Fixture repos are built in tests with real git; nothing is mocked.
- Results come from 2-3 real open-source repos, are reported in the README as measured, and include their limits.

## Known tensions with the brief (handled, not dropped)

- **CLAUDE.md said "grounded or silent".** The brief wants grade-D warnings. Resolution: grade D may raise VERIFY but never a blocking KEEP (`grade_floor_for_block = "C"`), and it always shows "reason unknown".
- **Bounded memory on 100k+ commits in Python.** Handled by streaming and per-batch commits. Line-level history is computed lazily at query time and cached, not precomputed for every line.
- **Calibration** needs labelled outcomes. Fence uses backtest outcomes as labels and reports a calibration table with sample sizes, rather than a claimed match.

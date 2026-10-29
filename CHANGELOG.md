# Changelog

All notable changes to Fence. Versions follow semantic versioning.

## 0.1.0 (unreleased)

### Edge cases and honest scores

- Symbol names match commit prose only with their exact case. A class `Repo` is no longer "named" by the word "repo".
- `why`, `check` and MCP tools index new commits first (amend, squash, new work). Before this, they explained those commits from the subject line alone and reported "reason unknown".
- In a shallow clone, the boundary commit's message is not offered as a change-level reason, because it did not add those lines.
- Binary files are refused with a clear error instead of being explained line by line.
- MCP `why` and `check` include `reason_scope`, matching JSON.
- Strength and Change Risk are labelled experimental (`score_note` in JSON and MCP, a note under `check` findings). The dormant "Outcome: …% regretted" line is removed.
- Parsing old Python with invalid escape sequences no longer prints `SyntaxWarning`s.

### Explanation quality and pooled evaluation

- `why` scopes commit, PR and issue text to the code it names. Multi-file commits no longer hand one reason sentence to every file. Change-level reasons are labelled `Inferred:` and graded C (`reason_scope`, `change_reason` in JSON). Only name-related tests count as evidence. Span labels name a symbol only if it contains the whole span.
- Pooled eval suite: `evals/repos.toml` (pinned SHAs), `evals/run_suite.py` (resumable), `evals/analyze.py` (Wilson and bootstrap intervals, leave-one-repo-out).
- Removed the shipped regret-rate calibration table, which pooled evidence contradicts. Thresholds and weights are unchanged.

### Precision and calibration round

- VERIFY now needs risk >= 50, except stated (A/B) decisions of high-stakes types (`scoring.high_stakes_types`).
- Low-signal paths (docs, config, completion scripts) count half (`scoring.low_signal_paths`).
- Keyword-inferred decision types (grade C/D) count half.
- Change Risk carries `regret_rate` and `regret_n` from a shipped isotonic calibration table. `python -m fence.scoring.calibration` refits it.
- The backtest reports observed rates per calibration bin, plus `calibration_monotonic`.
- Measured: noise is down on all three benchmark repos. Precision improved on the tuning set but not on held-out express. See README and STATUS.

### Initial features

First usable release.

- Incremental, crash-safe history index (SQLite), with rewrite and orphan detection, format-only commit detection, and PR mapping from merge and squash subjects.
- Language layer: Python (stdlib parser) plus JS, TS/TSX, Go and Java (tree-sitter), with content fingerprints.
- `fence why`: graded Decision Records (A-D) with verbatim reasons and explainable Fence Strength. Follows renames, reformats and cross-file moves.
- `fence check`: per-block KEEP / VERIFY / CLEAR / SAFE-TO-REMOVE-CANDIDATE, explainable risk, `fence.toml` policy, and an ack ledger (`.fence/acks.jsonl`).
- MCP server (`fence mcp`) with agent guardrail mode.
- GitHub enrichment (token optional): PRs, reviews, linked issues and labels.
- Git hooks, a GitHub Action with a sticky PR comment, `fence scan` and `fence report`.
- Obsolescence checks (stale-suspected), Ghost Fences (tombstones).
- `fence backtest`: Removal-Regret Backtest with precision, recall, noise, latency and calibration.

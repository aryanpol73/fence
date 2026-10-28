# Changelog

All notable changes to Fence. Versions follow semantic versioning.

## 0.1.0 (unreleased)

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

# Status

What is done, partial, or not done, each with the reason. Kept accurate to the code.

## Done

- **F1 indexing:** incremental SQLite index, crash-safe batches, resume, rewrite and orphan
  detection, shallow-clone and empty-repo reporting, format-only commit detection,
  `.git-blame-ignore-revs` read from the base revision, PR mapping from merge and squash
  subjects, and GitHub enrichment with ETag, rate-limit, pagination and request-budget
  handling. All covered by fixture tests.
- **Rename and move tracking:** git renames, `blame -w -C`, format-only skipping, plus
  Fence's own cross-file move follower. Attribution benchmark: 6/6 versus 3/6 for plain blame.
- **Languages:** Python (stdlib), JS, TS/TSX, Go and Java (tree-sitter). Other languages get
  file and line anchors.
- **F2 deterministic extraction:** signal catalog (text, label and code signals, each
  unit-tested), strict A-D grading, verbatim stated reasons, `Inferred:` marking, and
  injection-sentence filtering.
- **F3 why:** file, line, range or symbol targets; strongest-first or newest-first; reverts,
  regression chain, orphaned evidence; human and JSON (`fence/v1`) output.
- **F4 check:** working tree, staged, range, PR or diff file; delete/loosen/alter/move
  classification; explainable risk; policy file with path overrides; ack ledger with
  fingerprint invalidation and expiry; bypass env var; stable exit codes.
- **F5 interfaces:**
  - CLI: index, why, check, ack, explain, scan, report, doctor, config, backtest, hook, mcp,
    comment.
  - MCP server with guardrail mode.
  - Git hooks.
  - GitHub Action with a fork-safe sticky comment.
- **F6:** `scan` and `report` (markdown, JSON, HTML).
- **F7 / D3 obsolescence:** dependency removed or upgraded, feature flag gone, private symbol
  unreferenced, each yielding SAFE-TO-REMOVE-CANDIDATE.
- **D1 backtest:** no future leakage (`Context.as_of`); reports precision, recall, noise,
  latency, lead time and a calibration table.
- **D2 Ghost Fences:** tombstones for deleted guards, linked when the code comes back.
- **D5:** agent guardrail mode and the drop-in instruction snippet (README).
- **D6:** team ack ledger.
- **D7:** honest uncertainty: grades, "reason unknown", and confidence shown with every score.
- **D8 orphaned evidence:** rewritten commits and PRs or issues that cannot be fetched are
  marked as such.

## Partial

- **Calibration (target missed on held-out data).** Change Risk now carries an outcome
  calibration: the observed regret rate for its risk bin, from a static isotonic table fitted
  on cobra + requests (`fence/scoring/calibration.json`). On those repos the rate is
  monotonic: 2.0% / 3.3% / 8.8% for risk 0-29 / 30-50 / 51-100. On held-out express it is
  flat (1.7% / 1.9% / 1.9%), so it is not monotonic there.
  - Why it falls short: 27 regrets on the fitting set and 10 on express are too few to
    estimate rates that transfer between repos.
  - Evidence confidence (grade and coverage) is shown separately, labelled as such, and is
    not an outcome probability.
- **Backtest precision (2x target missed).** At 500 commits:
  - cobra: 2.85x lift, 43% recall.
  - requests: 1.6x lift, 25% recall.
  - held-out express: 0.96x lift, 20% recall.
  The tuning-set gains did not generalize. Per the overfitting guard, no further tuning was
  done against express.
- **Noise (5% target missed on two of three).** cobra warns on 6.2% of commits (was 12%),
  requests on 1.6% (was 2.2%), express on 7.4% (was 9.8%).
- **`check` latency** is 0.7-0.9s median and 2.0-2.8s p90 per commit on Windows (500-commit
  backtests). It can go over the 2-3s target on larger diffs, because line-history walks
  run once per decision. Hook mode skips them.
- **Blast radius** (caller counts) is used only for obsolescence, not as a Strength factor.
  Counting callers cheaply and correctly across languages needs a references index.
- **D4 regression echo.** Re-fixes of the same lines raise Strength (the regression-chain
  factor), but similar fixes across different lines are not detected.
- **"Same PR touches the evidence-linked tests"** is not weighted. Removed tests are.
- **Rebase-merged PRs** map to their PR only through the GitHub API.

## Not done

- **LLM synthesis pass (P2).** Optional by design. The `[llm]` config exists but nothing reads
  it, so enabling it does nothing. Left out to protect core quality within the time budget.
- **Web view (P2).** Lowest priority in the brief.
- **GitLab/Bitbucket.** The extension point is the `github/` package shape, but no adapter
  exists.
- **Dogfood findings on large (100k+) histories.** The index streams with bounded memory, but
  it has been run only on repos of up to about 6.5k commits.

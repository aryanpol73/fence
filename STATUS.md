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

- **Calibration.** Confidence reflects the evidence grade and coverage. It is not calibrated
  against outcomes: in the backtest, warned blocks at higher confidence were *not* regretted
  more often. Two reasons: the regret base rate is about 3%, and regret is a weak proxy for
  "the reason was right". It needs a labelled set of correct-reason judgments.
- **Backtest accuracy.** Warnings beat the base rate only modestly (about 1.2-1.4x), with small
  samples of regretted removals. The noise budget is met on requests but not on cobra or
  express (10-12% of commits warned). See the README for the numbers.
- **`check` latency** is about 1-1.5s median and 3-5.3s p90 per commit on Windows (before memoization). It goes
  over the 2-3s target on larger diffs, because line-history walks run once per decision.
  Hook mode skips them.
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

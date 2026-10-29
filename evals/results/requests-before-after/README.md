# requests: before and after explanation scoping

One Removal-Regret Backtest of psf/requests at the pinned SHA in `evals/repos.toml`
(500 first-parent commits, 300-commit regret window, local-git mode, no token).
`*.json` is the summary and `*.jsonl` has one line per removal block.

| | Fence code | KEEP | VERIFY | CLEAR | grade C | grade D | regretted warned |
|---|---|---|---|---|---|---|---|
| before | 4e27101 (before evidence scoping) | 10 | 8 | 96 | 38 | 72 | 1 of 4 |
| after | 2ee5a73 (scoping, exact-case symbols, edge-case fixes) | 9 | 6 | 99 | 28 | 82 | 1 of 4 |

Both runs have 114 removal blocks and 4 regretted. One repo shows no trend, and these
numbers are not evidence that the score predicts regret. The reason-detection change in
7f01f55 came later and is not reflected here.

## Reproduce

Each run takes 1-3 minutes once the clone is cached in `~/.cache/fence-evals`
(override with `FENCE_EVAL_CACHE`). The runner skips repos that already have a
`<name>.json`, so move the committed one aside first.

```bash
git worktree add ../fence-before 4e27101
cd ../fence-before
rm -f evals/results/requests.json
uv run --python 3.12 python evals/run_suite.py --only requests
```

For the "after" run, use the same commands with `2ee5a73` (or any later commit, for
current numbers). Output lands in `evals/results/requests.json` and `requests.jsonl` of
that checkout. Both runs used the same repo SHA and settings (see `sha`, `max_commits`
and `window` in each file). `after.json` has an empty `calibration` list because no
calibration table ships since 0524697.

Verdict and grade counts come from the `.jsonl` file:

```bash
uv run python -c "import json,collections,sys; r=[json.loads(l) for l in open(sys.argv[1])]; print(collections.Counter(x['verdict'] for x in r), collections.Counter(x['grade'] for x in r))" evals/results/requests.jsonl
```

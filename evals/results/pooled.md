# Pooled backtest

11 repos, 5500 commits, 5674 removal blocks, 178 regretted.

| Metric | Value (95% CI) |
|---|---|
| Base rate (regretted / blocks) | 3.1% [2.7%, 3.6%] (178/5674) |
| Precision (regretted / warned) | 3.5% [2.7%, 4.7%] (47/1334) |
| Lift (precision / base) | 1.12x, repo-bootstrap CI [0.47, 1.75] |
| Recall | 26.4% [20.5%, 33.3%] (47/178) |
| Commits warned | 6.8% [6.2%, 7.5%] (373/5500) |

## Regret rate by risk band

| Band | Regret rate (95% CI) |
|---|---|
| low | 4.2% [3.4%, 5.2%] (86/2038) |
| medium | 2.6% [2.0%, 3.2%] (74/2892) |
| high | 2.2% [1.3%, 3.6%] (14/638) |
| critical | 3.8% [1.5%, 9.3%] (4/106) |

## Per repo

| Repo | Blocks | Regretted | Warned | Lift | Recall | Commits warned |
|---|---|---|---|---|---|---|
| axios | 358 | 30 | 110 | 0.65 | 20% | 9.8% |
| click | 714 | 16 | 122 | 0.37 | 6% | 8.0% |
| cobra | 720 | 23 | 110 | 2.85 | 43% | 6.2% |
| express | 564 | 10 | 121 | 0.93 | 20% | 7.4% |
| flask | 930 | 25 | 173 | 0.22 | 4% | 7.6% |
| gin | 436 | 6 | 120 | 1.21 | 33% | 7.4% |
| gson | 831 | 5 | 271 | n/a | 0% | 12.0% |
| koa | 198 | 3 | 16 | n/a | 0% | 2.0% |
| logrus | 479 | 37 | 151 | 1.8 | 57% | 5.0% |
| requests | 114 | 4 | 18 | 1.58 | 25% | 1.6% |
| viper | 330 | 19 | 122 | 0.43 | 16% | 7.6% |

## Leave-one-repo-out calibration

| Held out | Regretted | Observed rate per refit bin | Non-decreasing | Top > bottom |
|---|---|---|---|---|
| axios | 30 | 0-55: 9.1%, 56-89: 6.4%, 90-100: - | False | False |
| click | 16 | 0-55: 2.3%, 56-89: 1.4%, 90-100: 0.0% | False | False |
| cobra | 23 | 0-55: 2.6%, 56-89: 7.8%, 90-100: - | True | True |
| express | 10 | 0-55: 1.8%, 56-89: 1.8%, 90-100: 0.0% | False | False |
| flask | 25 | 0-47: 3.3%, 48-55: 0.0%, 56-89: 1.0%, 90-100: - | False | False |
| gin | 6 | 0-55: 1.2%, 56-100: 2.1% | True | True |
| gson | 5 | 0-55: 0.8%, 56-89: 0.0%, 90-100: 0.0% | False | False |
| koa | 3 | 0-56: 1.6%, 57-89: 0.0%, 90-100: - | False | False |
| logrus | 37 | 0-89: 7.7%, 90-100: - | None | None |
| requests | 4 | 0-55: 3.0%, 56-89: 9.1%, 90-100: 0.0% | False | False |
| viper | 19 | 0-5: 62.5%, 6-55: 4.8%, 56-100: 3.2% | False | False |

Out of sample: top bin above bottom bin on 2/10 repos; non-decreasing on 2/10; lift > 1 on 4/11.

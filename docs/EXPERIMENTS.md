# Experiment log (model/v8)

Protocol: `docs/PROTOCOL.md` (pre-registered). Decision tool: `./accept.sh <base> <new>` after `./dev_eval.sh <tag>`.
Every experiment is logged, accepted or not. HOLD looks used: 0 / 40.

| # | Tag | Hypothesis / change | TUNE Δ [95 % CI] | lower bound | HOLD | Decision | Leakage checklist |
|---|---|---|---|---|---|---|---|
| E0 | (backbone only) | Leakage fixes in the v3 backbone: category model trained on months before the window, side files capped at the window end | −0.69 [−1.05, −0.38] on TUNE | – | – | fix kept (removes dev optimism; not a tuning decision) | fixes L2, L3 of docs/AUDIT.md |

# Experiment log (model/v8)

Protocol: `docs/PROTOCOL.md` (pre-registered). Decision tool: `./accept.sh <base> <new>` after `./dev_eval.sh <tag>`.
Every experiment is logged, accepted or not. HOLD looks used: 0 / 40.

| # | Tag | Hypothesis / change | TUNE Δ [95 % CI] | lower bound | HOLD | Decision | Leakage checklist |
|---|---|---|---|---|---|---|---|
| E0 | (backbone only) | Leakage fixes in the v3 backbone: category model trained on months before the window, side files capped at the window end | −0.69 [−1.05, −0.38] on TUNE | – | – | fix kept (removes dev optimism; not a tuning decision) | fixes L2, L3 of docs/AUDIT.md |
| B0 | base | Clean v8 baseline (v6c + leakage fixes) | TUNE 94.10 (alloc 95.25, P 98.58 / R 92.13; txn 97.05; exc 86.84) | – | HOLD 93.91 | reference | – |

Calibration: v6b (same output as v6c) scored LIVE 93.31 and 94.20 on the HOLD months with the leaky setup.
Clean HOLD 93.91 => leak worth ~0.3 there; honest HOLD -> LIVE gap ~0.6 (LIVE 95 <=> HOLD ~95.6; goal keeps 96.2).
Run time: ~570 s per window (both windows in parallel).
| D1 | (probe, no code) | Statement payments: missed lines with >= 10 golden items (TUNE, 1.66 alloc pts) clear a contiguous due-date window of the customer's open items | 274 of 281 lines have gaps (e.g. 685 paid, 763 open items skipped inside the span) | – | – | REJECTED before coding: set not recoverable from the amount without the advice | TUNE only |

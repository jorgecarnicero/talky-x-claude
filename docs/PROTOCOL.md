# Anti-overfitting protocol (model/v8) — pre-registered 2026-11-07, before the v8 baseline was scored

Why: the organisers judge overfitting strictly, FINAL is a single shot on new months (2026-01..06) with new masters
(maybe a new entity / ZAR), and LIVE has run ~0.9 below our dev numbers. Sources and reasoning:
`docs/research/overfitting.md`. Changing this file after experiments start must be logged with the reason.

## Windows (TRAIN months, scored with the official scorer restricted to the window)

| Window | Months | Use |
|---|---|---|
| TUNE | 2024-10..11 | free: per-segment tables, reading individual lines, diagnostics |
| HOLD | 2025-02..03 | accept / reject only. Totals only (no per-segment output is written). Partly spent: v4–v6 were tuned on it |
| GATE | 2025-04..05 | sealed. Run exactly ONCE, on a frozen commit whose hash is logged before the run |

Every learned table (payer directory, category model, opening balances, priors, any classifier) must use only data
dated before the start of the window being scored (checked by the leakage audit, `docs/AUDIT.md`).

## Acceptance rule for a change (all must hold)

1. TUNE: Δscore ≥ +0.05 (goal threshold) AND the lower bound of the one-sided 95 % payer-cluster paired bootstrap
   (`src/compare.py --cluster payer`, 2000 resamples) is > 0.
2. HOLD: Δscore ≥ −0.02 (goal threshold) AND Δ > −1 bootstrap SE. Only the accept/reject bit is used; HOLD totals are
   rounded to 0.01 in the log. Budget: 40 HOLD looks (counter in `docs/EXPERIMENTS.md`); when it is spent, HOLD
   decisions stop and only TUNE + the final GATE remain.
3. No segment with ≥ 100 TUNE lines drops > 2 points of accuracy; no Trace / Error in the logs.
4. Leakage checklist answered in the experiment log:
   - learned data only from before the scored window? no golden-derived input that FINAL won't have?
   - patterns / keywords found on TUNE only, never by reading HOLD / GATE lines?
   - no literal ids, IBANs, names, amounts or dates from the data in rules (grep check)?
   - every rule cites POLICIES.md / DATA_DICTIONARY.md, a banking convention, or a research card?
   - numeric thresholds are round and sit on a plateau (sweep of ±1 step shown on TUNE), not on a peak?
5. A change whose gain comes from a class / segment with < 30 TUNE lines is not accepted on that basis alone.

Rejected experiments are logged too (hypothesis, diff summary, TUNE CI, HOLD bit).

## Other rules

- Ablation: before the final freeze, remove each v8 change one at a time on TUNE; drop any with no significant gain.
- Robustness for FINAL: unseen-customer test (drop 25 % of customers from every learned table, compare their accuracy
  with seen customers) and a synthetic new-currency/entity smoke test; unknown codes must degrade gracefully.
- LIVE: a sanity monitor only. At most one upload per frozen release candidate (every 3 accepted changes). Before
  uploading, write down the predicted LIVE (HOLD − gap). A large miss triggers a leakage audit, never tuning.
- No change is ever accepted because of a LIVE score.
- GATE: done when HOLD target is reached; the result is reported whatever it is; no code change after GATE except
  reverting to the previous frozen version.
- Report for the organisers (`docs/MODEL_CARD.md` at the end): experiment count, HOLD-look count, window table, GATE
  result, unseen-customer gap, ablation table, grep proof of no instance constants, dev ↔ LIVE relation.

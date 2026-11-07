# Anti-overfitting protocol (model/v8)

Pre-registered 2026-11-07 before the v8 baseline was scored; amended the same day (see Changes) to merge with the
team's method `docs/ANTI_OVERFIT_team.md` (gates G1–G7, `solution/tools/significance.py`). Sources:
`docs/research/overfitting.md` and the team doc §1. Leakage audit: `docs/AUDIT.md`.

## Windows (TRAIN months, official scorer restricted to the window)

| Window | Months | Use |
|---|---|---|
| tune | 2024-10..11 | iterate freely: per-segment tables, reading lines, diagnostics |
| dev | 2025-04..05 | the team's dev window (n = 69,553): accept / reject only. We read totals and the gate verdict, never segments |
| sealed | 2025-01..02 | the team's sealed window: ONE run per accepted candidate (`sealed_check.sh`), only the sign is read |

2025-02..03 (our first HOLD) is no longer used: it overlaps the sealed window. Every learned table uses only data
dated before the start of the scored window; nothing dated after the window end is read (docs/AUDIT.md, fixed).

## Acceptance (all must hold; `./accept.sh <base> <new>` checks 1–4)

1. **TUNE composite:** Δ ≥ +0.05 AND the one-sided 95 % lower bound of the payer-cluster paired bootstrap
   (`src/compare.py`, 2000 resamples) > 0.
2. **Team gates G1–G3 on dev** (`significance.py` = ACEPTAR): McNemar z ≥ 3 and c − b ≥ 30 lines; payer sign test
   p ≤ 0.01 with no payer > 25 % of c − b; c − b > 0 in each month and ≥ 0 in each entity with > 1,000 lines.
3. **Dev composite:** Δ ≥ −0.02 AND > −1 bootstrap SE (the team gates measure lines, this guards the composite score,
   incl. the exception macro F1).
4. **TUNE segments:** none with ≥ 100 lines loses > 2 accuracy points; no Trace / Error.
5. **G4 sealed:** `./sealed_check.sh` once; the sign must match dev, else reject and change nothing.
6. **G5 literal sweep** on `git diff`: `grep -nE '20(24|25)-[0-9]{2}|BT-|ARI-|ARC-|API-|APC-|CUS[0-9]|SUP[0-9]|[0-9]+\.[0-9]{2}\b'`
   + a read of every new constant; each justified by POLICIES.md / DATA_DICTIONARY.md / a generic rule.
7. **G7:** each change behind its own env flag (default off until accepted) and fails soft (exception → skip, trunk
   output intact).
8. **Leakage checklist** answered in `docs/EXPERIMENTS.md`; thresholds round and on a plateau (±1 step); a gain coming
   only from a class / segment with < 30 lines does not count.

Verdict NEUTRO (no evidence) = not merged, no LIVE upload. Every experiment is logged, accepted or not.

## LIVE, FINAL and reporting

- **G6 LIVE:** one full CSV per accepted release candidate, handed to the user (we never upload); predicted LIVE
  written down first (dev − gap, gap ≈ 0.6 after the leakage fixes); same sign as dev required, never tuned on.
- Before the freeze: ablation (drop each accepted change on TUNE), unseen-customer test (25 % of customers removed from
  learned tables), synthetic new currency / entity smoke test, `--phase final` smoke test.
- Report: experiment count, dev looks, sealed runs, ablation table, unseen-customer gap, literal-sweep proof,
  dev ↔ LIVE relation.

## Goal mapping (user delegated the choice, 2026-11-07)

The /goal text's "HOLD" is now the dev window 2025-04..05 (target ≥ 96.2) and "GATE" is the sealed sign check plus
G6 on LIVE.

## Changes

- 2026-11-07: windows changed from TUNE/HOLD 2025-02..03/GATE 2025-04..05 to tune/dev/sealed because the team's
  method already uses 2025-04..05 as dev (so it could not be our sealed GATE) and 2025-01..02 as sealed (our HOLD
  overlapped February). HOLD had 1 look (the baseline); GATE was never run. Team gates G1–G7 added on top of ours.

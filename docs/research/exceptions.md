# Research: exception classification + F1-optimal decisions (Step R, 2026-11-07)

Scorer facts (bankrec_score.py): class F1 = 2TP/(Pred_c+Gold_c); a row is exc-TP when its item is in golden and the
label matches (amount NOT required); every other non-empty label is an FP (incl. rows with wrong items). One extra TP on
a rare class (OVERPAYMENT / SHORT_BANK_CHARGES) ~ 0.007-0.009 pts vs PARTIAL ~0.00015 and one txn line ~1e-4.
A class with golden rows that we never predict scores F1 = 0.

Sources: Lipton, Elkan & Narayanaswamy, Thresholding Classifiers to Maximize F1 (https://arxiv.org/abs/1402.1892):
optimal threshold = F1*/2. Narasimhan et al. ICML 2015 (https://proceedings.mlr.press/v37/narasimhanb15.html):
multiclass complex metrics. sklearn TunedThresholdClassifierCV docs. Munoz, Jalili & Tafakori 2025 JFDS (press page).

1. **Plug-in rule per (source pass x label)**: TRAIN confusion table p(gold | source, label), smoothed; keep label c
   only where precision > F1_c/2; enable optional rules whose precision exceeds it. Gain 0.1-0.4. S.
2. **R5 overpayment**: worth it if precision > ~0.4 (w_O ~30x w_U). Gain ~0.2 per 10 F1 pts. S.
3. **Policy consistency sweep** (POLICIES s.3): FX_DIFFERENCE on every invoice row (ARI and API) that clears an item in
   another currency (exc_fix only handles ARI); SHORT_BANK_CHARGES = non-SEPA, shortfall <= 60 EUR eq and <= 2 %, else
   PARTIAL. Gain 0.1-0.3. S-M.
4. **HGB second opinion** for short-pay classes, balanced weights, only where no exact policy match. 0.05-0.2. M.
5. **UNIDENTIFIED precision filter**: keep label only when P(gold=UNIDENTIFIED) > F1_U/2. 0.05-0.15. S-M.

Won't help: end-to-end ML (golden follows POLICIES deterministically); isotonic/Platt on a few hundred positives
(overfits; smoothed per-source rates are more robust); speculative extra rows (allocation FPs).

# Research backlog (model/v8)

Step R, round 1 (2026-11-07): four subagents researched one theme each (sources only, no code changes). Full cards with
sources live in `docs/research/`: `combinatorial.md`, `linkage.md`, `ltr.md`, `exceptions.md`. Overfitting / leakage
protocol: `docs/research/overfitting.md` (pending).

Scorer facts that drive the ranking (`starter/bankrec_score.py`):

- score = 50 · pair F1 + 30 · line accuracy + 20 · exception macro F1 (each class weighs 1/K whatever its size).
- 1 − pair F1 = (FP + FN) / (submitted + golden pairs): losses split exactly by segment (`src/pointslost.py`).
- Guessing a set beats abstaining when P(correct) > F1/2 ≈ 0.47 (Lipton et al. 2014). An abstained receipt is also an
  UNIDENTIFIED false positive when the golden has items.
- One extra TP on a rare exception class (OVERPAYMENT, SHORT_BANK_CHARGES) ≈ 0.007–0.009 pts; one line of accuracy
  ≈ 1e-4 pts on LIVE.

## Ranked backlog (gain / effort; gains are the agents' estimates, to be measured)

| # | Idea | Target | Est. gain | Effort | Card | Overfit risk |
|---|---|---|---|---|---|---|
| 1 | Per-(source pass × label) precision table → drop labels below F1_c/2, enable rules above (R5 etc.) | exception macro F1 | 0.1–0.4 | S | exceptions 1–2 | MED: thresholds learned from TRAIN months outside the windows only |
| 2 | Policy sweep: FX_DIFFERENCE on AP too, SHORT_BANK_CHARGES ≤ 60 EUR & ≤ 2 % non-SEPA | FX / bank charges | 0.1–0.3 | S | exceptions 3 | LOW: straight from POLICIES.md |
| 3 | Linked credit notes inside the subset search (CNs whose related_document ∈ S) | ar_1toN_credit_note | 0.3–0.8 | S | combinatorial 2 | LOW: POLICIES 5.9 |
| 4 | Cross-customer 1-edit reference index + amount confirmation | ref noise, unknown payer | 0.2–0.5 | S | linkage 1 | LOW |
| 5 | Payer directory keeps ambiguous keys (counts), abbreviation map, 2nd-word alias index | third-party payers | 0.1–0.3 | S | linkage 4 | MED: learned from TRAIN before the window only |
| 6 | Expected-value tie-break (month in text > oldest due > min days late) where P > 0.5 | same_amount_month, ambiguous multi | 0.5–1.5 | S | combinatorial 1 | MED: bucket probabilities from months before the window |
| 7 | Char n-gram TF-IDF top-k name candidates (weak, amount-confirmed) | no-candidate lines | 0.2–0.6 | S–M | linkage 2 | LOW |
| 8 | Customer behaviour priors (delay distribution, oldest-first rate, bundling) | ambiguous sets | 0.3–1.0 | S–M | ltr 2 | MED: past-only statistics |
| 9 | Joint per-customer MILP (scipy milp / HiGHS) with uniqueness re-solve | ar_bigpool, conflicts | 0.5–1.5 | M | combinatorial 3 | LOW |
| 10 | GBDT reranker over candidate sets + calibrated abstain | all ambiguous lines | 0.5–1.5 | M | ltr 1, combinatorial 5 | HIGH: train only on months before TUNE; ablate on HOLD + GATE |
| 11 | Meet-in-the-middle across related customers | ar_n_to_m | 0.3–0.7 | M | combinatorial 4 | LOW |
| 12 | Supervised Fellegi–Sunter customer scorer | payer choice, UNIDENTIFIED | 0.2–0.5 | M | linkage 3 | HIGH (learned) |
| 13 | Exception-label GBDT second opinion / UNIDENTIFIED precision filter | short-pay classes | 0.05–0.4 | M | exceptions 4–5, ltr 4 | HIGH (learned) |

Rejected by research: global MILP or exact cents DP (scale), Hungarian / min-cost flow (1:1 can't express subsets),
LambdaMART on top of a pointwise reranker (≤ +0.1), end-to-end ML or GNN matcher, phonetic codes, unsupervised EM,
isotonic calibration on a few hundred positives, speculative extra rows.

First diagnostics before building: (a) split missed golden pairs into "right set generated but not chosen" vs "never
generated"; (b) per-source × label confusion table; (c) `no_cand` / `cand_nomatch` counts in ar_match.

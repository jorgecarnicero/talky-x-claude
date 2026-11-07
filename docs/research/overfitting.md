# Research: overfitting, holdout reuse and leakage (2026-11-07)

- Dwork et al., Generalization in Adaptive Data Analysis and Holdout Reuse (https://arxiv.org/html/1506.02629):
  adaptive reuse overfits the holdout; Thresholdout reveals holdout only when train/holdout gap > T + noise, budget B.
- Blum & Hardt, The Ladder (https://arxiv.org/html/1502.04585v1): report only improvements > eta (or > s/sqrt(n),
  paired test), rounded; error grows only log in #submissions.
- Recht et al. 2019 (https://arxiv.org/html/1902.10811): drops on fresh test sets came from distribution gap, not
  adaptivity. Roelofs et al. NeurIPS 2019 Kaggle meta-analysis: little leaderboard overfitting. => main FINAL risk is shift.
- Kapoor & Narayanan 2023 (https://pmc.ncbi.nlm.nih.gov/articles/PMC10499856/): 8 leakage types; model info sheets.
  Ours: L1.2 (golden in opening ledger), L3.1 temporal (catmodel / side files / PDFs after window) -> docs/AUDIT.md.
- Kaufman, Rosset, Perlich KDD 2011: learn-predict separation (abstract via snippet).
- Hyndman FPP3 5.10 (https://otexts.com/fpp3/tscv.html): rolling origin; Bergmeir & Benitez 2012: blocked CV.
- Cawley & Talbot JMLR 2010 (https://www.jmlr.org/papers/v11/cawley10a.html): selection bias ~ size of differences
  between algorithms.
- Berg-Kirkpatrick et al. 2012 (https://aclanthology.org/D12-1091/): paired bootstrap; Dror et al. 2018
  (https://aclanthology.org/P18-1128/).
- Simmons et al. 2011; Gelman & Loken forking paths: fix stopping rules, report everything, robustness.
- Koh et al. WILDS (https://arxiv.org/abs/2012.07421): OOD performance substantially lower; group-level holdouts.

Protocol derived from this: docs/PROTOCOL.md. Red flags for a judge: literal constants, odd-precision or peaked
thresholds, learned tables using window/future data, many within-noise accepted changes, LIVE tracking each commit,
no rejected-experiment log, GATE run twice, macro-F1 gains from tiny classes, passes that fire in one window only,
no handling of unseen currencies / entities.

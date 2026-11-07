# Research: learning-to-rank / learned candidate scoring (Step R, 2026-11-07)

Sources opened: SAP patent US11645686B2 (https://patents.google.com/patent/US11645686B2/en, pairwise invoice classifier
-> "super-invoice" + set classifier); Burges MSR-TR-2010-82 (LambdaMART); Geifman & El-Yaniv 2017 selective
classification (https://arxiv.org/abs/1705.08500); Munoz, Jalili, Tafakori 2025 JFDS doi 10.1016/j.jfds.2025.100170
(press page only); sklearn HGB docs (monotonic_cst, categorical, sample_weight).

1. **GBDT reranker over the cascade's candidate sets + calibrated abstain** (SAP + selective classification).
   Log all candidate sets per line; label = equals golden set; features: residual abs/%, residual matches fee/discount/
   withholding, n items, contiguous due run, age-rank of items, (bank date - due) z-score vs customer history, name
   score, ref overlap, producing pass, customer oldest-first rate. HGB + monotonic cst; softmax within line; accept if
   p>=tau and margin>=delta, tau tuned for >=98 % precision on TUNE. Gain +0.5..1.5. Effort M. Runtime s.
2. **Customer behaviour priors** (delay distribution, P(oldest-first), items per payment, short-pay habits, IBANs),
   computed only from months before the line. As features or as a likelihood tie-break. Gain +0.3..1.0. Effort S-M.
3. **Invoice-pair affinity** (SAP): HGB on item pairs (same doc date/week, consecutive doc numbers, |due diff|, same PO
   prefix) to prune/rank subset-sum for bigpool / multi-invoice. Gain +0.5..1.5 if many-to-1 dominates misses. M-L.
4. **Exception-label second stage**: HGB with class-balanced weights, override rule label only above per-class tau.
   Gain +0.4..1.0. Effort S.
5. LambdaMART listwise: <= +0.1 over card 1 (2-10 candidates per line). Skip.

Won't help: end-to-end ML matcher / GNN (re-learns exact rules, runtime); forcing top-1 on every line (a wrong
allocation costs FP + FN + txn accuracy; accepts need >= ~95 % precision to be net positive).

**First diagnostic:** split missed golden pairs into (a) right set generated but not chosen vs (b) never generated.

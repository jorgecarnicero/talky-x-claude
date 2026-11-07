# Research: combinatorial matching (Step R, 2026-11-07)

Verified sources: Wu, Torres et al. "The Subset Sum Matching Problem", ECAI 2025, arXiv:2508.19218 (exact MILP,
greedy wrapper, meet-in-the-middle, DP; MILP times out beyond ~20-40 items); SAP patent US 11,645,686 (pairwise
invoice classifier -> super-invoices -> set classifier). Vendor blogs (Modern Treasury, Tesorio) unverified.

Current bigpool_solver: due-order prefix/window with <= 3 skips, CN none/all/one, one discount, claim only if unique
at min cost + decoy test. => recall lost to "unique at min cost" and per-receipt (not joint) solving.

1. **Expected-value tie-break** (derived from the scorer): pair F1 = 2TP/(2TP+FP+FN); guessing is +EV when
   p(correct) > F1/2 ~ 0.47; abstaining (UNIDENTIFIED) also costs an exception FP + a wrong line anyway. Pick by month
   in text > oldest open due > min total days past due; enable per tie-bucket where TUNE p > 0.5.
   Targets ar_same_amount_month, ambiguous ar_multi_late / ar_bigpool. Gain +0.5..1.5. Effort S.
2. **Linked credit notes inside the subset search** (POLICIES 5.9): option "exactly the open CNs whose related_document
   is in S"; prefix sums of linked CN along due order. Target ar_1toN_credit_note. Gain +0.3..0.8. Effort S.
3. **Joint per-customer MILP** (scipy.optimize.milp / HiGHS): all unmatched receipts of a customer in ~60 d x open
   items, x[r,i] binary, each item once, per-receipt equality with optional discount/charges slack; maximise explained
   receipts then min due inversions; uniqueness by re-solve with no-good cut; 0.2 s limit, process pool.
   Target ar_bigpool, conflicts. Gain +0.5..1.5. Effort M. Runtime ~1 min on 16 cores.
4. **Meet-in-the-middle across related customers** (group / IBAN / alias, networkx components): hash per-customer
   compact candidate sums. Target ar_n_to_m (~49 %). Gain +0.3..0.7. Effort M.
5. **Learned set ranker** instead of "unique at min cost" (top-K explanations, HGB). Gain +1..2 if it generalises. L.

Won't help: global MILP / exact cents DP (scale); Hungarian / min-cost flow (1:1 can't express subsets, precision
already 98 %).

# Leakage / overfitting audit (model/v8, 2026-11-07, read-only subagent)

No instance data hardcoded (no literal txn / item / customer ids, IBANs, names, amounts, batch ids) in src/, passes/,
solution/bankrec.py. Literals come from POLICIES.md, masters, spec phase dates or generic formats.

| Sev | Where | Finding | Fix | Status |
|---|---|---|---|---|
| HIGH (small) | src/data.py:238 `_golden_items` | pre-2022 items kept in the dev opening ledger only if they appear anywhere in the TRAIN golden, incl. inside / after the window (TUNE 15 paid in window, HOLD 9) | build `ever` from golden lines booked before dfrom | L1 |
| MED | solution/bankrec.py:1632-1690 | dev catmodel excludes only window months -> learns from later months | train on lines booked before the window start | L2 |
| MED | src/batches_io.py:17,25; bankrec.py:517,535,972 | side files (DD batches, cards, cheques, runs, advices) read up to window end + 1..2 months | cap at window end in train phase | L3 |
| MED | src/passes/remittance.py:85 | advices accepted up to 30 days after the last bank line | cap at dto in dev | L3 |
| MED | src/pdfinfo.py:28-64,151-173 | all TRAIN PDFs (IBAN-change notices, replacements, instalments) used with no date filter | filter document / effective date <= dto | L3 |
| MED | ar_match DUST_PCT 0.031, exc_fix ROUND_EXCESS, remittance DISPUTE_RATIOS, FRACTIONS | data-fitted constants | derive DUST_PCT from payment_terms; document the rest | L4 |
| MED | SUBMISSIONS.md, V4.md, ROADMAP.md | LIVE per-segment figures read as leaderboard tuning | v8 never tunes on LIVE (PROTOCOL.md) | doc |
| MED (FINAL) | bankrec.py:345-347 | masters_final REPLACES masters (data.py overlays) | overlay file by file | L5 |
| MED (FINAL) | phase-keyed caches (open_start_final, items_final, payer_dir, catmodel) | stale cache reuse | clear before FINAL (run script) | L5 |
| LOW | dup-detection window differs dev vs LIVE; payer_dir (correct, cutoff = window start); catmodel keyed by bank/code/subcode/sign/3 words (generalises); WITHHOLDING_ENTITIES, SEPA lists (POLICIES); search limits (KMAX, DUE_HI, ...) | document; freeze before GATE | doc |

Verdict: dev mostly trustworthy but optimistic; structural future-visibility likely part of the ~0.9 dev -> LIVE gap.

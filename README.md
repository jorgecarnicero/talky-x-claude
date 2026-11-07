# talky-x-claude — Bank reconciliation (Talky × Anthropic hackathon)

For every bank line: which open items (invoices / credit notes) it settles, how much, and why cash ≠ documents
(exception) — or, if no item, what it is (category). Rules: `data/docs/POLICIES.md`.

## Layout
```
repo/            this git repo (code only)
dataroot/        symlinks -> data/  (masters, sample, train, live, final, starter)   [outside git]
cache/           parquet caches (auto)                                                [outside git]
out/             submissions / dev outputs                                            [outside git]
```
Setup: `python3 -m venv --system-site-packages .venv && .venv/bin/pip install rapidfuzz pyarrow pandas scikit-learn pdfplumber openpyxl`

## Run
```
# dev window (tune here)
.venv/bin/python src/pipeline.py --phase train --from 2024-07-01 --to 2024-12-31 --out ../out/dev.csv.gz
.venv/bin/python src/evaluate.py ../out/dev.csv.gz --from 2024-07-01 --to 2024-12-31
# held-out window (check only, never tune on it)
.venv/bin/python src/pipeline.py --phase train --from 2025-01-01 --to 2025-06-30 --out ../out/holdout.csv.gz
.venv/bin/python src/evaluate.py ../out/holdout.csv.gz
# LIVE submission
.venv/bin/python src/pipeline.py --phase live --out ../out/live.csv.gz
```
`evaluate.py` wraps the **official** scorer (`starter/bankrec_score.py`).

## Architecture
`src/data.py` builds a `Ctx`: bank lines, all items, an **open-item ledger** (`ctx.open`), masters, side-file folders.
For TRAIN windows the opening balances are simulated from labels *before* the window — exactly what LIVE/FINAL give
in `open_items_at_start.csv.gz` (validated: 99.7 % agreement at 2025-07-01).
Passes in `src/passes/` run in order (`pipeline.PASS_ORDER`), each claims bank lines via `ctx.allocate()`;
unresolved lines fall back to UNIDENTIFIED (receipts) / BANK_FEES (payments).

## Validation protocol (anti-overfitting)
- dev window 2024-07..2024-12 for tuning; held-out 2025-01..2025-06 only for checks; LIVE leaderboard as external check.
- No hard-coded customer/supplier names, ids or amounts: rules come from POLICIES.md and the masters/side files.

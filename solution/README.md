# Our bank-reconciliation matcher

The data is **not in git** (see `.gitignore`). Each teammate extracts the archives into the repo root:

```
talky-x-claude/
├── masters/  sample/  train/  live/  (final/ when released)   <- from the hackathon archives, NOT tracked
├── starter/                   official starter + scorer (bankrec_score.py = server scorer)
└── solution/
    ├── bankrec.py             the matcher
    ├── tools/                 validate / rowloss / diagnose
    └── cache/                 auto-generated, NOT tracked
```

`bankrec-train-lite.tar.gz` contains `train/`, and `bankrec-live.tar.gz` contains `live/`. Move both up to the repo root.
`masters/`, `sample/` and `starter/` come from `bankrec-docs-and-masters.zip`. Python 3.10+ with the standard library
only. You also need `pdftotext` on PATH for PDF remittance advices (Git Bash on Windows ships it; on macOS run
`brew install poppler`).

## Iterate (run from the repo root)

Compare models on **dev**: held-out TRAIN months (2025-04 and 2025-05). Open balances at the start of the window
are rebuilt from the TRAIN labels, and the category model is retrained without those months. Dev is within ~0.7 points of
LIVE; sample overestimates by ~3.5.

```
python solution/bankrec.py --phase dev --out sub_dev.csv.gz --why why_dev.csv    # ~2 min (first run ~4 min: builds caches)
python solution/tools/score_dev.py sub_dev.csv.gz                                # official scorer on the window + per segment
python solution/tools/rowloss.py sub_dev.csv.gz --phase dev                      # where allocation rows are lost
python solution/tools/diagnose.py sub_dev.csv.gz why_dev.csv ar_ref_noise,ar_instalment --phase dev
```

Quick smoke test: `--phase sample` + `python starter/score_train.py sub_sample.csv.gz --sample` (~15 s).

## Submit

```
python solution/bankrec.py --phase live --out sub_live.csv.gz        # ~4 min, ~2.5 GB RAM
python solution/tools/validate.py sub_live.csv.gz --phase live       # must say "OK to upload"
```

FINAL is a single scored submission: run `--phase final` (it uses `final/masters_final/` automatically), then
validate it, then upload once.

## How the matcher works (passes in order; the first pass that explains a line wins)

1. **Batch files.** Direct-debit batches (batch id in the text, or account + total), card settlements (net total,
   items at gross), cheque deposits, and payment runs: `transfers` by end-to-end id, `aggregated` by total, and
   `confirming` by run id + latest due date moved to the next business day.
2. **Returns and explicit wording.** Direct-debit returns (mandate id + batch id + the receipt actually
   collected), returned cheques, card chargebacks, rejected supplier transfers; proforma → `CUSTOMER_ADVANCE`,
   duplicate refunds → `CUSTOMER_REFUND` / `SUPPLIER_REFUND`.
3. **Remittance advices** (csv / txt / xlsx / pdf). Matched by amount + date + payer or by reference. Deductions are
   classified as `SHORT_DISPUTE`, `SHORT_DISCOUNT` or `PARTIAL`.
4. **Document references in the text.** Full number, number without prefix, or digits only. When the text is
   truncated, the rest of the payment is completed from the payer's other open items.
5. **Payer identification** by IBAN or name (IDF token scoring), then amount matching over its open items: exact
   (oldest first), early-payment discount, withholding, FX, oldest-due prefix, small combinations, and prefix
   minus 1–2 items.
6. **Category model** for the rest: (bank, code, subcode, sign, text pattern) → category, learned from the TRAIN
   golden. Unmatched credits fall back to `UNIDENTIFIED`.

Scores: see `SUBMISSIONS.md`. Roadmap and explanation of the remaining work: `docs/ROADMAP.md`.

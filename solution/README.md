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

## LIVE simulation on TRAIN (preferred way to iterate; no submissions spent)

`solution/tools/dev.py` builds a LIVE-shaped dataset out of TRAIN (outside the repo, in `~/hackdata/data_dev`) and runs the
**unchanged** pipeline on it with `--phase live`, then scores it with the official scorer. Needs DuckDB only to build
the dataset (`pip install duckdb`) and `pdftotext` on PATH (without it PDF advices are skipped and the score is not comparable).

```
python solution/tools/dev.py --name my-idea            # engine v35 if src/pipeline.py exists (--engine v3 for v3 alone); window 2025-04-01..2025-05-31, ~2 min; writes dev_runs/my-idea/
python solution/tools/dev.py --name v3 --src /path/to/data     # --src: folder with train/ and masters/ (default ~/hackdata/data)
python solution/tools/compare.py v3 my-idea            # ACCEPT iff score up and no >90 % segment drops > 0.5 pts
python solution/tools/dev.py --start 2025-04-01 --end 2025-05-31 --out ~/hackdata/data_dev_mine --name my-idea   # own dataset dir: parallel runs do not clash
python solution/tools/dev.py --sealed --name final-check   # sealed window 2025-01-01..2025-02-28: use sparingly
python solution/tools/dev_acceptance.py                # harness self-tests (oracle = 100, all-UNIDENTIFIED, leak, opening table, determinism)
```

`dev_runs/<name>/`: `report.md`, `result.json`, `segments.csv`, `classes.csv` (per exception class: predicted rows, golden rows,
precision / recall / F1, "(n pequeño)" if < 50 cases), `failures_<segment>.csv` (every wrong bank line: text, golden, prediction,
which matcher produced it). Exception classes with < 100 golden rows are flagged as unreliable. The dataset layout is documented in `make_dev_dataset.py`; do not use the `segment` column in rules.

## Headroom: where the remaining points are (diagnostic, never feeds the pipeline)

```
python solution/tools/headroom.py v35 [--live live_segments.csv]   # notes/headroom_v35.{md,json} + one view per block (matching / excepciones / pdfs)
python solution/tools/headroom.py --compare v35 my-idea            # how the margin per segment / class moved, top-5 overlap
python solution/tools/inspect_txn.py v35 BT-2025-0092792           # one bank line: golden, prediction, pass, open items of the payer the pipeline identified
python solution/tools/inspect_txn.py v35 --segment ar_overpayment --worst 5
```

Margin = points the official score rises when a segment (or exception class) is replaced by the golden, re-scored with the official
scorer (exact). Recoverability (recall / ambiguity / impossible) uses the payer the pipeline identifies, not the golden's. The
segment -> block map is `solution/tools/blocks.yaml` (reporting only). `test_branch` informes end with the remaining margin of the top 5.
`live_segments.csv` (`segment,accuracy`) adds an "acc LIVE" column and flags segments where LIVE is > 3 points below dev.

### Test a teammate's branch with one command

```
solution/tools/test_branch.sh <branch> [--sealed] [--force]    # verdict ACEPTAR / NEUTRO / RECHAZAR + dev_runs/<branch>_<commit>/informe.md
solution/tools/test_branch.sh --merge <branch>                  # merge into the integration branch only if the last verdict for that commit was ACEPTAR
```

It fetches, builds a throw-away worktree of the branch (`~/hackdata/wt/`), trial-merges the integration branch (`model/v3-harness`,
override with `INTEGRATION_BRANCH`), runs `dev.py` on the merged code (harness and scorer always come from this checkout) with
`PYTHONHASHSEED=0` and a cache directory keyed by the hash of the cache-producing code (`~/hackdata/wt_data/<hash>-<window>`), and compares
with the baseline of the integration tip (generated once per tree hash, `dev_runs/_baseline_*`). Every test appends a row to
`dev_runs/registro.csv` (summary in `notes/registro_pruebas.md`). `--sealed` runs the sealed window once per tested tree
(`dev_runs/sealed_log.txt`). `--merge` also notes the branch in `SUBMISSIONS.md` as a LIVE candidate; it never uploads or pushes.
Only one test runs at a time (lock `~/hackdata/wt/.test.lock`); a second one waits. A merge conflict stops the test and lists the files.

**Reproducibility: pin `PYTHONHASHSEED`.** The matcher iterates over sets, so its output depends on Python's hash order (on the
dev window 84.26 vs 83.16 between two seeds). `dev.py` runs it with `PYTHONHASHSEED=0`; do the same for LIVE and **FINAL**
(`PYTHONHASHSEED=0 python solution/bankrec.py --phase final ...`) so that the file you validate is the file you upload, and
re-runs are identical.

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

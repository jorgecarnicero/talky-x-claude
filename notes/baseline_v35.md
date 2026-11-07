# Baseline v3.5 on the LIVE simulation (dev window 2025-04-01..2025-05-31)

Run: `python solution/tools/dev.py --name v35` (engine v35, `PYTHONHASHSEED=0`, dataset `~/hackdata/data_dev`).

## Score

**Dev 92.87** (alloc F1 0.9440, P 0.9771, R 0.9131, txn acc 0.9622, exception macro-F1 0.8403) vs **LIVE 91.92**: **+0.95**.
Same gap as v3 (dev 83.16 vs LIVE 82.25 = +0.91), so the simulation is consistently ~0.9 optimistic. 2,626 of 69,553 lines wrong.
Per-segment dev-minus-LIVE table: **not available**, we have no per-segment LIVE numbers for v3.5. Paste the LIVE result page
as `segment,accuracy` and run `python solution/tools/dev_vs_live.py v35 live_segments.csv --live-score 91.92`.

Time: 6.6 min cold (includes extracting 125k PDFs and advices into the cache), ~4.5 min warm (v3 77 s + passes 184 s).

## Leak audit (v3.5)

- v3.5 parquet caches (`items_<phase>`, `bank_<phase>`, `golden_train`, `docs_ra_<phase>`, `docs_pdf_<phase>`) were keyed by phase only and
  stored in `solution/cache/v35`, shared by every `--data`: a run on dev would reuse full-TRAIN / real-LIVE tables (and vice versa).
  Fixed in `dev.py`: `BANKREC_DATA` / `BANKREC_CACHE` point inside the dataset (`<data>/solution/cache/v35`, wiped on rebuild).
- `src/data.py` opened new items from the hard-coded date `2025-07-01`; now the first bank-line date of the phase (same in real LIVE).
- The dev dataset lacked document PDFs (used by v3.5): now split by `document_date` into `train/pdfs` and `live/pdfs`.
- Category model (v3) and AR matcher: trained only from `<data>/train` (cut before the window) or not trained at all; no absolute paths.
- Only external inputs: env `BANKREC_DATA`, `BANKREC_CACHE`, `V3_SUB`. Leak test: 36 checks pass.

## Weakest segments (lost lines)

| segment | n | accuracy | wrong |
|---|---:|---:|---:|
| ar_ref_noise | 14940 | 0.954 | 691 |
| ar_n_to_m | 600 | 0.387 | 368 |
| ar_1toN_credit_note | 1079 | 0.666 | 360 |
| ar_instalment | 1497 | 0.886 | 171 |
| ar_1toN_text | 2048 | 0.934 | 135 |
| ar_short_discount | 2355 | 0.949 | 119 |
| ar_discount_late | 269 | 0.680 | 86 |
| ar_overpayment | 135 | 0.370 | 85 |
| ar_same_amount_fifo | 2024 | 0.959 | 82 |
| ar_short_dispute | 348 | 0.796 | 71 |
| ar_fx | 263 | 0.734 | 70 |
| ar_same_amount_month | 1422 | 0.968 | 45 |

## Exception classes (F1, golden support; < 100 rows = unreliable)

| class | pred | golden | precision | recall | F1 | |
|---|---:|---:|---:|---:|---:|---|
| PARTIAL | 4440 | 4376 | 0.857 | 0.870 | 0.863 |  |
| OVERPAYMENT | 63 | 135 | 0.794 | 0.370 | 0.505 |  |
| SHORT_DISCOUNT | 5640 | 5997 | 0.999 | 0.940 | 0.969 |  |
| SHORT_BANK_CHARGES | 73 | 52 | 0.685 | 0.962 | 0.800 | (poco fiable: n<100) |
| SHORT_WITHHOLDING | 1190 | 1340 | 0.997 | 0.885 | 0.938 |  |
| SHORT_DISPUTE | 831 | 976 | 1.000 | 0.851 | 0.920 |  |
| DUPLICATE_PAYMENT | 244 | 249 | 0.885 | 0.868 | 0.876 |  |
| UNIDENTIFIED | 2957 | 1638 | 0.547 | 0.988 | 0.705 |  |
| RETURNED | 1480 | 1478 | 0.980 | 0.982 | 0.981 |  |
| FX_DIFFERENCE | 6266 | 5726 | 0.810 | 0.887 | 0.847 |  |

## Passes, ordered by wrongly explained lines

| pass | lines | accuracy | wrong | worst segments |
|---|---:|---:|---:|---|
| v3rest | 3002 | 0.539 | 1383 | ar_ref_noise(373); ar_n_to_m(311); ar_1toN_credit_note(130) |
| v3 | 29794 | 0.980 | 608 | ar_1toN_credit_note(126); ar_instalment(123); ar_ref_noise(116) |
| ar | 4989 | 0.883 | 585 | ar_ref_noise(201); ar_1toN_credit_note(104); ar_1toN_text(71) |
| batches | 22001 | 0.998 | 42 | ar_dd_returned(27); ar_dd_batch(5); ap_duplicate_refund(2) |
| remittance | 2170 | 0.996 | 8 | ar_short_discount(6); ar_discount_late(2) |
| ap_match | 2747 | 1.000 | 0 |  |
| nonitem | 4850 | 1.000 | 0 |  |

(`v3` / `v3rest` = v3 matcher lines before / after the specialised passes; `ar` = new AR matcher. Detail per sub-matcher: `dev_runs/v35/passes_detail.csv`.)

## Lines left without any pass

2,957 lines (**4.2 %**) end as UNIDENTIFIED (v3's default category, `v3rest:cat`); **1,619 are correct (54.8 %)**, 1,338 are wrong.
UNIDENTIFIED precision 0.547, recall 0.988 (1,638 in the golden): the default almost never misses a real UNIDENTIFIED but
swallows ~1,300 receipts that do have items, mostly in `ar_ref_noise`, `ar_n_to_m` and `ar_1toN_credit_note`. That is the biggest pool left (1,383 of the 2,626 wrong lines).

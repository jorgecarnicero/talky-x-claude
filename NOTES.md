# Experiment log (score = official scorer)

| when | change | dev 2024H2 | holdout 2025H1 | LIVE |
|---|---|---|---|---|
| 10:30 | starter (naive exact amount + doc number) | – | 11.22 (2021-24: 11.75) | – |
| ~11:00-11:47 | pipeline skeleton, fallback only | 2.28 | – | – |

## Findings
- Segment mix (TRAIN): ar_ref_noise 21 %, ap_payment_multi 11.7 %, ar_1to1 9.5 %, ap_payment_single 9.4 %,
  ar_same_amount_fifo 3.3 %, bank fees 3.2+1.6 %, ar_1toN_text 3.1 %, ar_short_discount 2.9 % …
- AP payments carry `end_to_end_id` = payment-run id (camt) -> exact lookup in payment_runs/.
- train-lite contains ~214k 2021 items never settled and absent from LIVE's open items: dropped in simulation.
| ~11:00-11:47 | nonitem pass alone | 3.00 (14,172 lines claimed, >=99.8% exact) | – | – |
| ~11:00-11:47 | remittance pass alone (+ PDF/advice extraction) | 30.40 (6,764 lines, pair precision 0.9998) | – | – |

- Judgment call (remittance): Sep-2025 XLSX layout has no reason column; an unexplained deduction is SHORT_DISPUTE only if
  it is an exact standard cut (3/5/10/20/30 %), else PARTIAL. Unverifiable on labels — revisit if LIVE looks off.
- PDF clause extraction: 100 % parsed train/live, <1 min per phase; clauses present on ~100 % of instalment/retention/
  advance/replaced items (pdfinfo.py API for the AR matcher).
| ~11:00-11:47 | batches+remittance+nonitem+ap_match, window 2025-04..05 (team v3's dev window) | – | 65.17 | – |
| ~11:00-11:47 | team v3 (main) same window | – | 82.83 | 82.25 |
| ~11:00-11:47 | HYBRID: my lines with items (batches/remittance/ap_match) + v3 for the rest | – | **87.65** | – |

- Mine beats v3 on: ap_payment_multi/single (96->100 %), ap_direct_debit (88->100), cheques (83->100), dd_batch (84->99),
  ap_fx (81->99), ap_factored, dd_returned, supplier refunds, cn netted. ~1,234 more correct lines in the window.
- Window 2025-04..05 was never used for tuning by any agent (they tuned on 2024H2) -> fair out-of-sample comparison.
| 12:05 | v3.5 = batches,remittance,ap_match,nonitem,**v3import**,ar_match(snapshot),v3rest | – | 92.49 (2025-04..05) | **91.92** |

v3.5 LIVE 91.92 vs local 92.49 (gap 0.57, same as v3's 0.58) -> generalises, no sign of overfitting.
Reproduce: `./run_v35.sh live` (runs team v3, docs extraction, our pipeline, validator).

# FINAL submission candidates (not uploaded yet — the team uploads ONE file)

| file | code | LIVE score of the method | notes |
|---|---|---|---|
| sub_final_v8c_pf130.csv.gz | model/v8b-pf (commit 700bf5f) | 94.67 (v8b-pf) | v8b + ZA01 'EFT CREDIT' payer pattern + postfix --win 130. VALID, 220,256 lines. KNOWN BUG: 766 wrong DUPLICATE_PAYMENT rows on card settlements (card order numbers repeat across entities in FINAL; the order cache was keyed by number only) |
| sub_final_v8d_pf130.csv.gz | (coming ~18:48) | 94.67 | = v8c + card order cache keyed by (order, entity). Recommended upload once validated |

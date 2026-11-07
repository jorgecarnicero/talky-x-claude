# Research: record linkage / payer identification (Step R, 2026-11-07)

Code facts: name aliases are blocked on entity + exact first word, exact-prefix match (>= 10 chars); fuzzy refs only
inside a known customer (cust_refs), global_refs exact only; payer_dir drops keys mapping to > 1 customer.
First diagnostic: no_cand / cand_nomatch counts on TUNE (ceiling for cards 1, 2, 4).

1. **Cross-customer 1-edit reference index + amount confirmation** (SymSpell https://github.com/wolfgarbe/SymSpell;
   Damerau: > 80 % of errors are 1 edit). Accept only if amount independently agrees (Fellegi-Sunter: joint coincidence
   tiny). Target ar_ref_noise / unknown payer. Gain +0.2..0.5. S.
2. **Char n-gram TF-IDF top-k name blocking** (Cohen, Ravikumar & Fienberg 2003 SoftTFIDF; ING sparse_dot_topn),
   re-scored with rapidfuzz; weak candidates only, precision from amount checks. Gain +0.2..0.6. S-M.
3. **Supervised Fellegi-Sunter candidate scorer** (Splink FS + term-frequency docs; dedupe docs): LR/HGB on
   (line, candidate customer) pairs from months BEFORE the window. Gain +0.2..0.5. M.
4. **Payer directory keeping ambiguous keys (with counts) + mined abbreviation map + 2nd-word alias index**. +0.1..0.3. S.
5. Check digits / glued numbers in refs. +0.05..0.15. S (only if misses show it).

Won't help: unsupervised EM (we have labels); phonetic codes / neural embeddings; Jaro-Winkler on whole text.
Christen 2012 TKDE indexing survey: blocking key choice is the main lever.

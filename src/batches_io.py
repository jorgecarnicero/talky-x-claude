"""Loading of side files (direct-debit batches, card settlements, cheque deposits, payment runs) for the
batches / ap_match passes. Robust to missing folders, unseen columns and bad files (logged, skipped)."""
import glob
import os
import re

import pandas as pd

AMOUNT_COLS = {"batch_total", "amount", "gross", "fee", "net", "deposit_total", "transfer_amount", "document_amount"}


def _month_of(fname):
    m = re.search(r"_(\d{6})(?:_|\.)", os.path.basename(fname))
    return m.group(1) if m else None


def load_side(ctx, sub, months_before=8, months_after=2):
    """Concatenate side files <phase>/<sub>/*.csv for every phase dir in ctx.side_dirs, restricted to files whose
    YYYYMM lies in [window start - months_before, window end + months_after]. Cached in ctx.cache."""
    key = ("side", sub)
    if key in ctx.cache:
        return ctx.cache[key]
    if len(ctx.bank):
        d0 = pd.Timestamp(ctx.bank.booking_date.min()) - pd.DateOffset(months=months_before)
        d1 = pd.Timestamp(ctx.bank.booking_date.max()) + pd.DateOffset(months=months_after)
        lo, hi = d0.strftime("%Y%m"), d1.strftime("%Y%m")
        if getattr(ctx, "horizon", None):  # dev window: no side files after the window end
            hi = min(hi, ctx.horizon[:7].replace("-", ""))
    else:
        lo, hi = "000000", "999999"
    frames, nbad = [], 0
    seen = set()
    for sd in ctx.side_dirs:
        for f in sorted(glob.glob(os.path.join(sd, sub, "**", "*.csv"), recursive=True)):
            base = os.path.basename(f)
            mo = _month_of(f)
            if mo and not (lo <= mo <= hi):
                continue
            if base in seen:   # same file present in two phase dirs
                continue
            try:
                d = pd.read_csv(f, dtype=str, keep_default_na=False)
                d.columns = [c.strip().lower() for c in d.columns]
                d["_file"] = base
                frames.append(d)
                seen.add(base)
            except Exception as e:  # noqa: BLE001
                nbad += 1
                print(f"    [batches_io] skip {f}: {e}")
    if frames:
        df = pd.concat(frames, ignore_index=True, sort=False)
        for c in AMOUNT_COLS & set(df.columns):
            df[c] = pd.to_numeric(df[c].str.replace(",", ".", regex=False), errors="coerce")
    else:
        df = pd.DataFrame()
    print(f"    [batches_io] {sub}: {len(frames)} files, {len(df)} rows ({nbad} bad)")
    ctx.cache[key] = df
    return df

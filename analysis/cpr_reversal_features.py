"""What separates the 63 strong CPR bear-trap REVERSALS from downside breaks that
just keep falling?  Feature-mining with a control group.

CASE  (n~63) : red downside-first CPR break -> reclaimed top -> ran >=0.5% above it.
CONTROL(n~175): red downside-first CPR break that NEVER reclaimed the top
                (the break held -> clean PUT continuation).  Same setup, opposite
                outcome -- so any feature that differs is a candidate *cause* of the
                reversal, not just NIFTY's up-bias.

Every feature is measured AT THE BREAK CANDLE using only data up to that bar
(no lookahead) -- i.e. it would be knowable live, at PUT-entry time.  Volatility:
realized vol + day-open ATM IV from the chain (no India VIX in the feed).

Ranked by Cohen's d = (mean_case - mean_ctrl) / pooled_sd.  |d|>=0.5 = a real,
usable separation; |d|<0.2 = noise.
"""
from datetime import time
import numpy as np
import pandas as pd

from engine.data.spot import SpotSeries
from engine.data.catalog import Catalog

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
CUTOFF = time(14, 45)
CONT_PCT = 0.50                # strong-reversal threshold (% above top after reclaim)

print("Loading spot (5min) + catalog ...")
spot = SpotSeries.from_index(ROOT, "5min")
df = spot._df.copy()
df["day"] = df["DateTime"].dt.normalize()

# ---- daily frame + rolling context (prior-days-only, no lookahead) -----------
g = df.groupby("day")
D = pd.DataFrame({"open": g.Open.first(), "high": g.High.max(),
                  "low": g.Low.min(), "close": g.Close.last()}).sort_index()
D["ret"] = np.log(D.close / D.close.shift(1))
D["sma20"] = D.close.rolling(20).mean().shift(1)      # shift(1) = through yesterday only
D["sma50"] = D.close.rolling(50).mean().shift(1)
tr = pd.concat([D.high - D.low, (D.high - D.close.shift()).abs(),
                (D.low - D.close.shift()).abs()], axis=1).max(axis=1)
D["atr14"] = tr.rolling(14).mean().shift(1)
D["rv10"] = (D.ret.rolling(10).std() * np.sqrt(252) * 100).shift(1)   # ann. %, prior
D["low5"] = D.low.rolling(5).min().shift(1)
D["low10"] = D.low.rolling(10).min().shift(1)
D["prev_close"] = D.close.shift(1)
D["prev_low"] = D.low.shift(1)
D["prev_ret"] = D.ret.shift(1)
_lows10 = [D.low.shift(k) for k in range(1, 11)]       # last 10 daily lows (for the shelf count)

# ---- day-open ATM IV loader (front expiry, DTE>=3), cached per expiry file ----
cat = Catalog(ROOT)
_exp = sorted(pd.Timestamp(e) for e in cat.expiries())
_iv_cache = {}
def iv_at_open(d):
    day = pd.Timestamp(d).normalize()
    fronts = [e for e in _exp if (e - day).days >= 3]
    if not fronts:
        return np.nan
    exp = min(fronts)
    key = exp.strftime("%Y-%m-%d")
    if key not in _iv_cache:
        p = cat.path_for(key)
        try:
            t = pd.read_parquet(p, columns=["DateTime", "IV"])
            t["d"] = pd.to_datetime(t.DateTime).dt.normalize()
            t["tm"] = pd.to_datetime(t.DateTime).dt.time
            opn = t[t.tm == time(9, 15)]
            _iv_cache[key] = opn.groupby("d").IV.median()   # ATM IV at 09:15 per date
        except Exception:
            _iv_cache[key] = pd.Series(dtype=float)
    return float(_iv_cache[key].get(day, np.nan))


def cpr(h, l, c):
    p = (h + l + c) / 3.0; bc = (h + l) / 2.0; tc = 2.0 * p - bc
    return max(tc, bc), min(tc, bc)


rows = []
for day, gg in df.groupby("day"):
    gg = gg.sort_values("DateTime")
    dt0 = gg["DateTime"].iloc[0].to_pydatetime()
    hlc = spot.prev_day_hlc(dt0)
    if hlc is None or day not in D.index:
        continue
    ph, pl, pc = hlc
    top, bottom = cpr(ph, pl, pc)
    bars = list(gg.itertuples(index=False))

    trig_i = trig_side = None
    for i, b in enumerate(bars):
        if b.DateTime.time() >= CUTOFF:
            break
        if b.Low <= top and b.Close > top:
            trig_i, trig_side = i, "CALL"; break
        if b.High >= bottom and b.Close < bottom:
            trig_i, trig_side = i, "PUT"; break
    if trig_side != "PUT":
        continue
    bk = bars[trig_i]
    if bk.Close >= bk.Open:                       # RED break candles only
        continue

    # outcome label
    recl = next((j for j in range(trig_i + 1, len(bars)) if bars[j].Close > top), None)
    reclaimed = recl is not None
    post_high = max(x.High for x in bars[recl:]) if reclaimed else bk.Close
    run_pct = 100.0 * (post_high - top) / top if reclaimed else 0.0
    strong = reclaimed and bars[-1].Close > top and run_pct >= CONT_PCT
    if not (strong or (not reclaimed)):           # keep CASE(strong) and CONTROL(never reclaimed)
        continue
    label = "case" if strong else "control"

    # ---- features at the break bar (knowable live) ----
    row = D.loc[day]
    day_open = bars[0].Open
    rng = bk.High - bk.Low
    body = abs(bk.Close - bk.Open)
    lower_wick = min(bk.Open, bk.Close) - bk.Low
    morn = np.array([x.Close for x in bars[:trig_i + 1]], dtype=float)
    morn_rv = (np.std(np.diff(np.log(morn))) * np.sqrt(75 * 252) * 100
               if len(morn) > 2 else np.nan)       # ann. % from 5-min morning bars
    shelf = sum(abs(float(s.get(day)) - bk.Close) / bk.Close <= 0.005
                for s in _lows10 if pd.notna(s.get(day)))

    rows.append(dict(
        date=day.date(), label=label, run_pct=run_pct,
        depth_pct=100 * (bottom - bk.Close) / bottom,       # how far below band it closed
        brk_body_pct=100 * body / day_open,
        brk_range_pct=100 * rng / day_open,
        brk_lower_wick=lower_wick / rng if rng > 0 else 0,  # rejection tail (buyers)
        brk_close_pos=(bk.Close - bk.Low) / rng if rng > 0 else 0,
        break_min=(bk.DateTime - bars[0].DateTime).total_seconds() / 60.0,
        gap_pct=100 * (day_open - row.prev_close) / row.prev_close,
        open_vs_top=100 * (day_open - top) / top,
        ret_open_to_break=100 * (bk.Close - day_open) / day_open,
        prev_ret=100 * row.prev_ret,
        above_sma20=100 * (row.prev_close - row.sma20) / row.sma20,
        above_sma50=100 * (row.prev_close - row.sma50) / row.sma50,
        dist_low5=100 * (bk.Low - row.low5) / row.low5,     # proximity to prior 5-day low
        dist_low10=100 * (bk.Low - row.low10) / row.low10,
        near_prev_low=100 * (bk.Low - row.prev_low) / row.prev_low,
        support_shelf=shelf,                                # # of last-10 lows within 0.5%
        rv10=row.rv10,
        atr14_pct=100 * row.atr14 / row.prev_close,
        morn_rv=morn_rv,
        iv_open=100 * iv_at_open(day),                      # day-open ATM IV, %
        dow=day.dayofweek,
    ))

F = pd.DataFrame(rows)
C, K = F[F.label == "case"], F[F.label == "control"]
print(f"\nCASE (strong reversal): {len(C)}   CONTROL (break held / no reclaim): {len(K)}"
      f"   span {F.date.min()} -> {F.date.max()}\n")

FEATS = ["depth_pct", "brk_body_pct", "brk_range_pct", "brk_lower_wick", "brk_close_pos",
         "break_min", "gap_pct", "open_vs_top", "ret_open_to_break", "prev_ret",
         "above_sma20", "above_sma50", "dist_low5", "dist_low10", "near_prev_low",
         "support_shelf", "rv10", "atr14_pct", "morn_rv", "iv_open"]

def cohend(a, b):
    a, b = a.dropna(), b.dropna()
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return np.nan
    sp = np.sqrt(((na - 1) * a.var() + (nb - 1) * b.var()) / (na + nb - 2))
    return (a.mean() - b.mean()) / sp if sp > 0 else np.nan

tab = []
for f in FEATS:
    tab.append((f, C[f].mean(), K[f].mean(), C[f].median(), K[f].median(), cohend(C[f], K[f])))
T = pd.DataFrame(tab, columns=["feature", "case_mean", "ctrl_mean", "case_med", "ctrl_med", "d"])
T["absd"] = T.d.abs()
T = T.sort_values("absd", ascending=False)

print("=== FEATURE SEPARATION: CASE (reversal) vs CONTROL (break held) ===")
print("Cohen's d>0 => higher on reversal days.  |d|>=0.5 strong, 0.2-0.5 modest, <0.2 noise.\n")
hdr = f"{'feature':<18}{'case_mean':>11}{'ctrl_mean':>11}{'case_med':>10}{'ctrl_med':>10}{'d':>7}"
print(hdr); print("-" * len(hdr))
for _, r in T.iterrows():
    star = " *" if r.absd >= 0.5 else (" ." if r.absd >= 0.2 else "")
    print(f"{r.feature:<18}{r.case_mean:>11.3f}{r.ctrl_mean:>11.3f}"
          f"{r.case_med:>10.3f}{r.ctrl_med:>10.3f}{r.d:>7.2f}{star}")

print("\n=== DAY-OF-WEEK MIX (Mon=0) ===")
dow = pd.crosstab(F.dow, F.label, normalize="columns") * 100
print(dow.round(0).to_string())

print("\n=== TOP DISCRIMINATORS (|d|>=0.3), plain reading ===")
for _, r in T[T.absd >= 0.3].iterrows():
    hi = "higher" if r.d > 0 else "lower"
    print(f"  reversal days have {hi:>6} {r.feature:<16} "
          f"(case {r.case_med:+.2f} vs control {r.ctrl_med:+.2f}, d={r.d:+.2f})")

# ---- MULTIVARIATE: can ANY combination predict the reversal out-of-sample? -----
# Pure-numpy logistic regression, 5-fold CV AUC. AUC 0.5 = coin flip (no signal),
# 0.7+ = usefully predictive. In-sample AUC always flatters; CV is the honest read.
Xdf = F[FEATS].copy()
for f in FEATS:                                     # median-impute NaNs, standardize
    Xdf[f] = Xdf[f].fillna(Xdf[f].median())
X = ((Xdf - Xdf.mean()) / Xdf.std().replace(0, 1)).values
y = (F.label.values == "case").astype(float)

def fit_logit(Xt, yt, iters=800, lr=0.1, l2=1.0):
    w = np.zeros(Xt.shape[1]); b = 0.0; n = len(yt)
    for _ in range(iters):
        z = Xt @ w + b; p = 1 / (1 + np.exp(-z)); err = p - yt
        w -= lr * (Xt.T @ err / n + l2 * w / n); b -= lr * err.mean()
    return w, b

def auc(sc, yt):
    pos, neg = sc[yt == 1], sc[yt == 0]
    if len(pos) == 0 or len(neg) == 0:
        return np.nan
    r = pd.Series(sc).rank().values
    return (r[yt == 1].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))

rng = np.random.default_rng(0)
idx = rng.permutation(len(y)); folds = np.array_split(idx, 5)
cv = []
for k in range(5):
    te = folds[k]; trn = np.concatenate([folds[j] for j in range(5) if j != k])
    w, b = fit_logit(X[trn], y[trn])
    cv.append(auc(X[te] @ w + b, y[te]))
w_all, _ = fit_logit(X, y)
print("\n=== MULTIVARIATE PREDICTABILITY (logistic, 5-fold CV) ===")
print(f"  cross-validated AUC : {np.nanmean(cv):.3f}  (folds {', '.join(f'{c:.2f}' for c in cv)})")
print(f"  in-sample AUC       : {auc(X @ w_all, y):.3f}   (0.50 = no signal, 0.70+ = useful)")
coef = pd.Series(w_all, index=FEATS).sort_values(key=np.abs, ascending=False)
print("  largest standardized weights:", ", ".join(f"{k}:{v:+.2f}" for k, v in coef.head(6).items()))

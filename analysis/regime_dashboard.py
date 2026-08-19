"""Self-contained HTML dashboard for the NIFTY buy/sell regime detector.

Reads analysis/nifty_daily.csv + analysis/india_vix.csv, computes the LIVE
regime factors + the current label (regime_finder + regime_vol), pulls the
straddle-thermometer GO-gate results from analysis/regime_straddle_pnl_out.txt,
and writes a single self-contained analysis/regime_dashboard.html — no server,
no external deps, opens in any browser. Re-run whenever the CSVs refresh.

    PYTHONPATH=. python3 analysis/regime_dashboard.py
"""
from __future__ import annotations

import re
from datetime import datetime, time

import numpy as np
import pandas as pd

from analysis.regime_finder import label_regimes
from analysis.regime_vol import (expanding_percentile, sharpen_labels,
                                 sharp_state, VIX_PCT_WARMUP)

from analysis.regime_data import load_closes_vix
STRADDLE_OUT = "analysis/regime_straddle_pnl_out.txt"
OUT_HTML = "analysis/regime_dashboard.html"


# ------------------------------------------------------------------ #
# factor + result extraction
# ------------------------------------------------------------------ #
def _live_factors():
    closes, vix = load_closes_vix()

    lab = label_regimes(closes)
    returns = closes.pct_change()
    rv = returns.rolling(20).std()
    rv_z = (rv - rv.rolling(90).mean()) / rv.rolling(90).std().replace(0.0, np.nan)
    ma200 = closes.rolling(200).mean()
    slope = ma200 - ma200.shift(20)
    vix_pct = expanding_percentile(vix.reindex(lab.index).astype(float), VIX_PCT_WARMUP)

    sharp = sharpen_labels(closes, vix)
    last = sharp.index[-1]
    state = sharp.apply(sharp_state, axis=1)

    row = sharp.loc[last]
    direction = "Bull" if row["bull"] else ("Bear" if row["bear"] else "Neutral")
    mood = "Stormy" if row["stormy"] else ("Calm" if row["calm"] else "Mixed")

    return {
        "closes": closes, "vix": vix, "sharp": sharp, "state": state, "last": last,
        "close": float(closes.loc[last]),
        "ma200": float(ma200.loc[last]), "slope": float(slope.loc[last]),
        "rv": float(rv.loc[last]), "rv_z": float(rv_z.loc[last]),
        "vix_v": float(vix.loc[last]), "vix_pct": float(vix_pct.loc[last]),
        "bull": bool(row["bull"]), "bear": bool(row["bear"]),
        "stormy": bool(row["stormy"]), "calm": bool(row["calm"]),
        "fear_rising": bool(row["fear_rising"]),
        "direction": direction, "mood": mood, "regime": state.iloc[-1],
        "data_start": sharp.index[0], "n_labelled": len(sharp),
    }


def _episodes(state, sharp, min_len=5, last_n=8):
    rid = (state != state.shift()).cumsum()
    runs = [(seg.index[0], seg.index[-1], len(seg), seg.iloc[0])
            for _, seg in state.groupby(rid) if len(seg) >= min_len]
    out = []
    for a, b, n, s in runs[-last_n:]:
        fear = bool(sharp.loc[b, "fear_rising"])
        out.append((a.date().isoformat(), b.date().isoformat(), n, s, fear))
    return out


def _parse_thermometer():
    """Parse the committed straddle-thermometer output into structured rows."""
    try:
        txt = open(STRADDLE_OUT).read()
    except OSError:
        return None
    header = re.search(r"episodes:\s*(\d+) labeled,\s*(\d+) with data,\s*"
                       r"(\d+) priced,\s*(\d+) skipped", txt)
    line = re.compile(r"^\s*(.+?)\s+n=\s*(\d+)\s+net=\s*([-\d,]+)\s+"
                      r"win%=\s*(\d+)%\s+PF=\s*([\d.]+)", re.M)
    rows = {}
    for m in line.finditer(txt):
        rows[m.group(1).strip()] = {
            "n": int(m.group(2)), "net": m.group(3),
            "win": int(m.group(4)), "pf": float(m.group(5)),
        }
    return {"header": header.groups() if header else None, "rows": rows}


# ------------------------------------------------------------------ #
# HTML rendering
# ------------------------------------------------------------------ #
CSS = """
:root{--bg:#0f1117;--card:#181b24;--line:#262a36;--txt:#e6e8ee;--mut:#8b93a7;
--bull:#2ecc71;--bear:#ff5c6c;--calm:#4aa3ff;--storm:#ffb23e;--neutral:#8b93a7;
--nogo:#ff5c6c;--go:#2ecc71;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,Helvetica,Arial,sans-serif;padding:28px;max-width:1180px;margin:0 auto}
h1{font-size:22px;font-weight:700;letter-spacing:-.2px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.8px;color:var(--mut);
margin:0 0 12px;font-weight:600}
.sub{color:var(--mut);font-size:12.5px;margin-top:4px}
.grid{display:grid;gap:16px;grid-template-columns:1fr 1fr;margin-top:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px}
.full{grid-column:1/-1}
.hero{display:flex;align-items:center;gap:22px;flex-wrap:wrap}
.regime{font-size:40px;font-weight:800;letter-spacing:-.5px}
.chip{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;
font-weight:600;border:1px solid transparent}
.b-bull{background:rgba(46,204,113,.14);color:var(--bull);border-color:rgba(46,204,113,.35)}
.b-bear{background:rgba(255,92,108,.14);color:var(--bear);border-color:rgba(255,92,108,.35)}
.b-calm{background:rgba(74,163,255,.14);color:var(--calm);border-color:rgba(74,163,255,.35)}
.b-storm{background:rgba(255,178,62,.14);color:var(--storm);border-color:rgba(255,178,62,.35)}
.b-neutral{background:rgba(139,147,167,.14);color:var(--neutral);border-color:rgba(139,147,167,.3)}
.b-nogo{background:rgba(255,92,108,.16);color:var(--nogo);border-color:rgba(255,92,108,.4);
font-size:15px;padding:6px 14px}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);font-size:13px}
th{color:var(--mut);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.5px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.factor-val{font-weight:700;font-variant-numeric:tabular-nums}
.pos{color:var(--bull)}.neg{color:var(--bear)}
.pending td{color:var(--mut)}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden}
.bar>span{display:block;height:100%}
.foot{color:var(--mut);font-size:12px;margin-top:8px}
.warn{background:rgba(255,178,62,.1);border:1px solid rgba(255,178,62,.3);
border-radius:10px;padding:10px 14px;color:var(--storm);font-size:12.5px;margin-top:16px}
.key{color:var(--mut);font-size:12px}
"""


def _chip(label, kind):
    return f'<span class="chip b-{kind}">{label}</span>'


def _mood_kind(mood):
    return {"Calm": "calm", "Stormy": "storm"}.get(mood, "neutral")


def _dir_kind(d):
    return {"Bull": "bull", "Bear": "bear"}.get(d, "neutral")


def _factors_table(f):
    slope_txt = "rising" if f["slope"] > 0 else ("falling" if f["slope"] < 0 else "flat")
    dist = (f["close"] / f["ma200"] - 1) * 100
    rows = [
        ("Direction — close vs 200-DMA + slope",
         f'{f["close"]:,.0f} vs {f["ma200"]:,.0f} ({dist:+.1f}%), MA {slope_txt}',
         _chip(f["direction"], _dir_kind(f["direction"]))),
        ("Realized vol (RV) — 20d, z-scored vs 90d",
         f'z = {f["rv_z"]:+.2f}  (σ<sub>20d</sub> = {f["rv"]*100:.2f}%/day)',
         _chip("high" if f["rv_z"] > 0.5 else ("low" if f["rv_z"] < -0.5 else "mid"),
               "storm" if f["rv_z"] > 0.5 else ("calm" if f["rv_z"] < -0.5 else "neutral"))),
        ("Implied vol (IV) — India VIX, expanding %ile",
         f'VIX = {f["vix_v"]:.2f}  ({f["vix_pct"]*100:.0f}th %ile)',
         _chip("high" if f["vix_pct"] >= 0.66 else ("low" if f["vix_pct"] <= 0.33 else "mid"),
               "storm" if f["vix_pct"] >= 0.66 else ("calm" if f["vix_pct"] <= 0.33 else "neutral"))),
        ("Blend — calm=BOTH quiet, stormy=EITHER loud",
         "IV and RV must agree for calm", _chip(f["mood"], _mood_kind(f["mood"]))),
        ("Fear direction — VIX vs 10 sessions ago",
         "rising = fear building", _chip("rising" if f["fear_rising"] else "cooling",
                                         "storm" if f["fear_rising"] else "calm")),
        ("Persistence — 4-of-5 confirmation", "applied to calm/stormy/direction",
         '<span class="key">on</span>'),
    ]
    body = "".join(
        f'<tr><td>{n}</td><td class="factor-val">{v}</td><td style="text-align:right">{c}</td></tr>'
        for n, v, c in rows)
    return f'<table><thead><tr><th>Factor</th><th>Value</th><th class="num">State</th>'\
           f'</tr></thead><tbody>{body}</tbody></table>'


def _pending_table():
    rows = [
        ("IV / RV ratio (variance risk premium, ~21d tenor)",
         "near-tautological for a straddle GO-gate; rich-IV proxy gave only 1 SELL episode"),
        ("Option skew / greeks", "available in Data-v3 chain; not wired"),
        ("Market breadth", "needs new data; not wired"),
        ("Vol dynamics (term-structure / acceleration)", "designed, not wired"),
    ]
    body = "".join(f'<tr class="pending"><td>{n}</td><td>{d}</td></tr>' for n, d in rows)
    return f'<table><thead><tr><th>Designed, not implemented</th><th>Why / status</th>'\
           f'</tr></thead><tbody>{body}</tbody></table>'


def _thermometer_html(t):
    if not t:
        return '<p class="foot">straddle-thermometer output not found.</p>'
    order = [("SELL/short (calm)", "theta on calm — the SELL side"),
             ("BUY/long (storm)", "gamma on storm — the BUY side"),
             ("COMBINED", "the regime strategy"),
             ("always-short", "null: sell everything"),
             ("always-long", "null: buy everything"),
             ("random", "null: coin-flip side")]
    trs = []
    for key, desc in order:
        r = t["rows"].get(key)
        if not r:
            continue
        neg = r["net"].startswith("-")
        cls = "neg" if neg else "pos"
        strong = ' style="font-weight:700"' if key == "COMBINED" else ""
        trs.append(
            f'<tr{strong}><td>{key}<div class="key">{desc}</div></td>'
            f'<td class="num">{r["n"]}</td>'
            f'<td class="num {cls}">{"" if neg else "+"}{r["net"]}</td>'
            f'<td class="num">{r["win"]}%</td>'
            f'<td class="num {cls}">{r["pf"]:.2f}</td></tr>')
    hdr = ""
    if t["header"]:
        lab, dat, pri, skp = t["header"]
        hdr = f'<p class="foot">{lab} episodes labelled &middot; {pri} priced &middot; {skp} skipped</p>'
    return (f'<table><thead><tr><th>Arm</th><th class="num">n</th>'
            f'<th class="num">Net ₹</th><th class="num">Win%</th>'
            f'<th class="num">PF</th></tr></thead><tbody>{"".join(trs)}</tbody></table>{hdr}')


def _calendar_html(state):
    vc = state.value_counts()
    total = int(vc.sum())
    rows = []
    for name, cnt in vc.items():
        pct = cnt / total * 100
        mood = name.split("_")[0]
        color = {"Calm": "var(--calm)", "Stormy": "var(--storm)"}.get(mood, "var(--neutral)")
        rows.append(
            f'<tr><td>{name}</td><td class="num">{cnt}</td>'
            f'<td style="width:45%"><div class="bar"><span style="width:{pct:.0f}%;'
            f'background:{color}"></span></div></td></tr>')
    return f'<table><tbody>{"".join(rows)}</tbody></table>'


def _episodes_html(eps):
    trs = []
    for a, b, n, s, fear in reversed(eps):
        mood = s.split("_")[0]
        trs.append(
            f'<tr><td>{a} &rarr; {b}</td><td class="num">{n}d</td>'
            f'<td>{_chip(s, _mood_kind(mood))}</td>'
            f'<td class="key">{"fear ↑" if fear else "fear ↓"}</td></tr>')
    return f'<table><tbody>{"".join(trs)}</tbody></table>'


def build_html():
    f = _live_factors()
    t = _parse_thermometer()
    eps = _episodes(f["state"], f["sharp"])
    gen = datetime.now()

    last_d = f["last"].date()
    provisional = (last_d == gen.date() and gen.time() < time(15, 30))
    warn = (f'<div class="warn">⚠ Latest bar ({last_d}) is from an OPEN session '
            f'(generated {gen:%H:%M}) — today’s close is provisional. '
            f'A slow structural label is unlikely to flip, but re-run after 15:30 IST '
            f'for the settled value.</div>') if provisional else ""

    hero = f'''<div class="card full">
      <h2>Current regime &mdash; as of {last_d}</h2>
      <div class="hero">
        <div class="regime">{f["regime"].replace("_", " ")}</div>
        <div>{_chip(f["mood"], _mood_kind(f["mood"]))} {_chip(f["direction"], _dir_kind(f["direction"]))}
          {_chip("fear rising" if f["fear_rising"] else "fear cooling",
                 "storm" if f["fear_rising"] else "calm")}</div>
      </div>
      <p class="sub">NIFTY {f["close"]:,.0f} &middot; India VIX {f["vix_v"]:.2f} &middot;
        {"quiet, low-vol" if f["calm"] else ("elevated-vol" if f["stormy"] else "transitional")}
        {"down-trend" if f["bear"] else ("up-trend" if f["bull"] else "trendless")} regime.</p>
      {warn}
    </div>'''

    verdict = ""
    if t and t["rows"].get("COMBINED"):
        c = t["rows"]["COMBINED"]
        ashort = t["rows"].get("always-short", {})
        verdict = (f'<p class="foot" style="margin-bottom:14px">'
                   f'{_chip("GO-GATE: NO-GO", "nogo")} &nbsp; regime timing '
                   f'(COMBINED PF {c["pf"]:.2f}) loses to dumb always-short '
                   f'(PF {ashort.get("pf","?")}). The edge is short-vol premium, not the regime.</p>')

    body = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NIFTY Regime Detector — Dashboard</title><style>{CSS}</style></head><body>
  <h1>NIFTY Buy/Sell Regime Detector</h1>
  <p class="sub">Two-axis vol-state detector (direction &times; IV/RV mood) &middot;
    data {f["data_start"].date()} &rarr; {last_d} &middot;
    {f["n_labelled"]:,} labelled days &middot; generated {gen:%Y-%m-%d %H:%M}</p>
  <div class="grid">
    {hero}
    <div class="card full"><h2>Live factors &amp; current values</h2>{_factors_table(f)}
      <p class="foot">All six factors are computed by the running code and feed today’s
      label. IV (India VIX) and RV (realized vol) are used as two <em>separate agreeing axes</em>.</p>
    </div>
    <div class="card"><h2>GO-gate results — straddle thermometer</h2>
      {verdict}{_thermometer_html(t)}</div>
    <div class="card"><h2>Regime calendar (labelled history)</h2>{_calendar_html(f["state"])}</div>
    <div class="card full"><h2>Recent regime episodes (&ge;5 days)</h2>{_episodes_html(eps)}</div>
    <div class="card full"><h2>Designed but not wired</h2>{_pending_table()}
      <p class="foot">Listed for honesty — these are in the design spec but NOT in the
      running detector. The IV/RV <em>ratio</em> is deliberately excluded (near-tautological
      for a straddle gate); IV and RV <em>individually</em> are live above.</p>
    </div>
  </div>
</body></html>'''
    return body


def main():
    html = build_html()
    with open(OUT_HTML, "w") as fh:
        fh.write(html)
    print(f"wrote {OUT_HTML} ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

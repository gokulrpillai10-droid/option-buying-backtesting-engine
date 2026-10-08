"""Generates analysis/iv_rv_analysis_dashboard.html from iv_rv_intraday.parquet."""
import json, warnings, datetime as _dt
warnings.filterwarnings("ignore")

import pandas as pd, numpy as np
from pathlib import Path

SIG_PATH = Path(__file__).parent / "iv_rv_intraday.parquet"
OUT_PATH = Path(__file__).parent / "iv_rv_analysis_dashboard.html"

df = pd.read_parquet(SIG_PATH)
df["_date"] = df["DateTime"].dt.date
df["_time"] = df["DateTime"].dt.time
df["_year"] = df["DateTime"].dt.year
df["_month"] = df["DateTime"].dt.to_period("M").astype(str)

snap = df[df["_time"] == _dt.time(9, 47)].copy()
snap["iv_pct"] = snap["iv"] * 100

# ── Build data ──────────────────────────────────────────────────────────────
iv_dist = []
for label, lo, hi in [("<8%",0,.08),("8-12%",.08,.12),("12-15%",.12,.15),
                       ("15-20%",.15,.20),("20-25%",.20,.25),(">25%",.25,999)]:
    m = (snap["iv"] >= lo) & (snap["iv"] < hi)
    iv_dist.append({"label": label, "count": int(m.sum()), "pct": round(m.mean()*100,1)})

ivrv_dist = []
for label, lo, hi in [("<0.75",0,.75),("0.75-1",.75,1.0),("1.0-1.25",1.0,1.25),
                       ("1.25-1.5",1.25,1.5),("1.5-2",1.5,2.0),(">2",2.0,999)]:
    m = (snap["iv_rv_30"] >= lo) & (snap["iv_rv_30"] < hi)
    ivrv_dist.append({"label": label, "count": int(m.sum()), "pct": round(m.mean()*100,1)})

yearly = []
for yr, g in snap.groupby("_year"):
    yearly.append({
        "year": int(yr), "days": int(len(g)),
        "med_iv":  round(float(g["iv_pct"].median()), 1),
        "p10_iv":  round(float(g["iv_pct"].quantile(.1)), 1),
        "p90_iv":  round(float(g["iv_pct"].quantile(.9)), 1),
        "med_ivrv": round(float(g["iv_rv_30"].median()), 2),
        "pct_iv_lt12":  round(float((g["iv"] < .12).mean()*100), 1),
        "pct_iv_lt20":  round(float((g["iv"] < .20).mean()*100), 1),
        "pct_ivrv_lt1": round(float((g["iv_rv_30"] < 1.0).mean()*100), 1),
    })

monthly = []
for m, g in snap.groupby("_month"):
    monthly.append({
        "month": m,
        "med_iv":       round(float(g["iv_pct"].median()), 1),
        "med_ivrv":     round(float(g["iv_rv_30"].median()), 2),
        "pct_ivrv_lt1": round(float((g["iv_rv_30"] < 1.0).mean()*100), 1),
    })

entry = df[df["_time"] >= _dt.time(9, 47)].copy()
entry["_bucket"] = entry["DateTime"].dt.floor("30min").dt.time
tod = []
for b, g in entry.groupby("_bucket"):
    tod.append({
        "time": str(b)[:5],
        "med_iv":       round(float(g["iv"].median()*100), 1),
        "med_ivrv":     round(float(g["iv_rv_30"].median()), 2),
        "pct_iv_lt12":  round(float((g["iv"] < .12).mean()*100), 1),
        "pct_ivrv_lt1": round(float((g["iv_rv_30"] < 1.0).mean()*100), 1),
    })

samp = snap.dropna(subset=["iv","rv_30"]).sample(min(1200, len(snap)), random_state=42)
scatter = []
for r in samp.itertuples(index=False):
    iv_v = float(r.iv) * 100
    rv_v = float(r.rv_30) * 100
    if iv_v <= 55 and rv_v <= 55:
        scatter.append({"iv": round(iv_v, 2), "rv": round(rv_v, 2)})

summary = {
    "total_days":    int(snap["_date"].nunique()),
    "date_range":    f"{snap['_date'].min()} to {snap['_date'].max()}",
    "med_iv":        round(float(snap["iv_pct"].median()), 1),
    "med_ivrv":      round(float(snap["iv_rv_30"].median()), 2),
    "pct_iv_lt12":   round(float((snap["iv"] < .12).mean()*100), 1),
    "pct_iv_lt20":   round(float((snap["iv"] < .20).mean()*100), 1),
    "pct_ivrv_lt1":  round(float((snap["iv_rv_30"] < 1.0).mean()*100), 1),
}

DATA = dict(summary=summary, iv_dist=iv_dist, ivrv_dist=ivrv_dist,
            yearly=yearly, monthly=monthly, tod=tod, scatter=scatter)

# ── Year table rows ─────────────────────────────────────────────────────────
def cls_iv12(v):
    return "pos" if v > 20 else ""
def cls_iv20(v):
    return "pos" if v > 70 else ""
def cls_ivrv(v):
    return "pos" if v > 20 else "warn" if v > 10 else ""

yr_rows = ""
for r in yearly:
    yr_rows += (
        f'<tr>'
        f'<td>{r["year"]}</td><td>{r["days"]}</td>'
        f'<td class="hi">{r["med_iv"]}%</td>'
        f'<td>{r["p10_iv"]}%</td><td>{r["p90_iv"]}%</td>'
        f'<td>{r["med_ivrv"]}x</td>'
        f'<td class="{cls_iv12(r["pct_iv_lt12"])}">{r["pct_iv_lt12"]}%</td>'
        f'<td class="{cls_iv20(r["pct_iv_lt20"])}">{r["pct_iv_lt20"]}%</td>'
        f'<td class="{cls_ivrv(r["pct_ivrv_lt1"])}">{r["pct_ivrv_lt1"]}%</td>'
        f'</tr>\n'
    )

# ── HTML ────────────────────────────────────────────────────────────────────
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>NIFTY IV / IV-RV Analysis 2020-2026</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f1117;color:#e2e8f0;font-family:'Inter',system-ui,sans-serif;padding:24px}}
h1{{font-size:22px;font-weight:700;color:#f8fafc;margin-bottom:4px}}
.sub{{color:#94a3b8;font-size:13px;margin-bottom:28px}}
.cards{{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-bottom:28px}}
.card{{background:#1e2233;border:1px solid #2d3748;border-radius:10px;padding:16px 14px}}
.card-val{{font-size:26px;font-weight:700;color:#38bdf8}}
.card-val.green{{color:#4ade80}}
.card-val.amber{{color:#fbbf24}}
.card-lbl{{font-size:11px;color:#94a3b8;margin-top:4px;line-height:1.4}}
.row2{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:28px}}
.row3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;margin-bottom:28px}}
.panel{{background:#1e2233;border:1px solid #2d3748;border-radius:10px;padding:20px}}
.panel-title{{font-size:12px;font-weight:600;color:#cbd5e1;margin-bottom:14px;letter-spacing:.05em;text-transform:uppercase}}
canvas{{display:block}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th{{background:#131825;color:#94a3b8;text-align:right;padding:7px 10px;font-weight:600;letter-spacing:.04em;font-size:11px}}
th:first-child{{text-align:left}}
td{{padding:7px 10px;text-align:right;border-bottom:1px solid #1e2a3a;color:#e2e8f0}}
td:first-child{{text-align:left;color:#94a3b8}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#252d42}}
.pos{{color:#4ade80!important;font-weight:600}}
.hi{{color:#38bdf8!important;font-weight:600}}
.warn{{color:#fbbf24!important}}
.full-row{{margin-bottom:28px}}
.note{{font-size:11px;color:#64748b;margin-top:8px}}
</style>
</head>
<body>
<h1>NIFTY Options &#8212; IV &amp; IV/RV Analysis</h1>
<p class="sub">Daily 09:47 open snapshot &bull; {summary["date_range"]} &bull; {summary["total_days"]} trading days</p>

<div class="cards">
  <div class="card"><div class="card-val">{summary["total_days"]}</div><div class="card-lbl">Trading days analysed</div></div>
  <div class="card"><div class="card-val">{summary["med_iv"]}%</div><div class="card-lbl">Median IV at open (09:47)</div></div>
  <div class="card"><div class="card-val">{summary["med_ivrv"]}x</div><div class="card-lbl">Median IV / RV&#8321;&#8320;</div></div>
  <div class="card"><div class="card-val green">{summary["pct_iv_lt12"]}%</div><div class="card-lbl">Days with IV &lt; 12%</div></div>
  <div class="card"><div class="card-val amber">{summary["pct_iv_lt20"]}%</div><div class="card-lbl">Days with IV &lt; 20%</div></div>
  <div class="card"><div class="card-val green">{summary["pct_ivrv_lt1"]}%</div><div class="card-lbl">Days IV/RV &lt; 1.0 (cheap)</div></div>
</div>

<div class="row2">
  <div class="panel">
    <div class="panel-title">IV Distribution at Open (09:47)</div>
    <canvas id="ivDist" width="460" height="200"></canvas>
  </div>
  <div class="panel">
    <div class="panel-title">IV / RV&#8321;&#8320; Distribution at Open</div>
    <canvas id="ivrvDist" width="460" height="200"></canvas>
  </div>
</div>

<div class="full-row">
  <div class="panel">
    <div class="panel-title">Year-by-Year Summary</div>
    <table>
      <tr>
        <th>Year</th><th>Days</th>
        <th>Median IV</th><th>P10</th><th>P90</th>
        <th>Median IV/RV</th>
        <th>IV&lt;12%</th><th>IV&lt;20%</th><th>IV/RV&lt;1.0</th>
      </tr>
      {yr_rows}
    </table>
  </div>
</div>

<div class="row3">
  <div class="panel">
    <div class="panel-title">Monthly Median IV (%)</div>
    <canvas id="monthIV" width="340" height="200"></canvas>
    <p class="note">Mar 2020 COVID spike capped at 60%</p>
  </div>
  <div class="panel">
    <div class="panel-title">Monthly Median IV/RV</div>
    <canvas id="monthIVRV" width="340" height="200"></canvas>
  </div>
  <div class="panel">
    <div class="panel-title">% Days IV/RV &lt; 1.0 per Month</div>
    <canvas id="monthPct" width="340" height="200"></canvas>
  </div>
</div>

<div class="row2">
  <div class="panel">
    <div class="panel-title">Time-of-Day — Median IV/RV &amp; % Days IV/RV &lt; 1.0</div>
    <canvas id="tod" width="460" height="220"></canvas>
  </div>
  <div class="panel">
    <div class="panel-title">IV vs RV&#8321;&#8320; Scatter (09:47, capped 55%)</div>
    <canvas id="scatter" width="460" height="220"></canvas>
    <p class="note">Green = IV/RV &lt; 1.0 &nbsp; Red = IV/RV &gt; 1.5 &nbsp; Grey = 1.0-1.5 &nbsp; Dashed = IV=RV line</p>
  </div>
</div>

<script>
const DATA = {json.dumps(DATA)};

function setup(id,w,h){{
  const c=document.getElementById(id),dpr=window.devicePixelRatio||1;
  c.width=w*dpr;c.height=h*dpr;c.style.width=w+"px";c.style.height=h+"px";
  const ctx=c.getContext("2d");ctx.scale(dpr,dpr);
  return {{ctx,w,h}};
}}

// ── Horizontal bar ──────────────────────────────────────────────────────────
function hbar(id,items,colors){{
  const {{ctx,w,h}}=setup(id,460,200);
  const pad={{l:72,r:90,t:12,b:10}};
  const bh=19,gap=7;
  const maxP=Math.max(...items.map(d=>d.pct));
  const bw=w-pad.l-pad.r;
  items.forEach((d,i)=>{{
    const y=pad.t+i*(bh+gap);
    const bl=bw*(d.pct/maxP)*0.94;
    ctx.fillStyle=colors[i]||"#60a5fa";
    ctx.beginPath();ctx.roundRect(pad.l,y,bl,bh,3);ctx.fill();
    ctx.fillStyle="#94a3b8";ctx.font="11px Inter,system-ui";ctx.textAlign="right";
    ctx.fillText(d.label,pad.l-6,y+bh/2+4);
    ctx.fillStyle="#e2e8f0";ctx.textAlign="left";
    ctx.fillText(d.pct+"% ("+d.count+"d)",pad.l+bl+6,y+bh/2+4);
  }});
}}

hbar("ivDist",DATA.iv_dist,
  ["#1d4ed8","#2563eb","#3b82f6","#60a5fa","#93c5fd","#bfdbfe"]);
hbar("ivrvDist",DATA.ivrv_dist,
  ["#4ade80","#86efac","#64748b","#475569","#f97316","#ef4444"]);

// ── Line chart ──────────────────────────────────────────────────────────────
function lineChart(id,W,H,seriesList,opts){{
  opts=opts||{{}};
  const {{ctx,w,h}}=setup(id,W,H);
  const pad={{t:20,r:16,b:28,l:40}};
  const cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;
  const allVals=seriesList.flatMap(s=>s.data.filter(v=>v!==null&&isFinite(v)));
  let mn=opts.min!==undefined?opts.min:Math.min(...allVals);
  let mx=opts.max!==undefined?opts.max:Math.max(...allVals);
  if(mn===mx){{mn-=1;mx+=1;}}
  const rng=mx-mn, n=seriesList[0].data.length;

  ctx.strokeStyle="#1e2a3a";ctx.lineWidth=1;
  for(let i=0;i<=5;i++){{
    const y=pad.t+ch*(1-i/5);
    ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(pad.l+cw,y);ctx.stroke();
    ctx.fillStyle="#64748b";ctx.font="10px Inter,system-ui";ctx.textAlign="right";
    ctx.fillText((mn+rng*(i/5)).toFixed(1),pad.l-4,y+3);
  }}
  const step=Math.max(1,Math.floor(n/7));
  seriesList[0].labels.forEach((lbl,i)=>{{
    if(i%step===0||(i===n-1)){{
      const x=pad.l+cw*(i/(n-1));
      ctx.fillStyle="#64748b";ctx.font="9px Inter,system-ui";ctx.textAlign="center";
      ctx.fillText(lbl.slice(0,7),x,h-pad.b+11);
    }}
  }});
  seriesList.forEach(s=>{{
    ctx.strokeStyle=s.color;ctx.lineWidth=s.lw||1.8;
    if(s.dash)ctx.setLineDash(s.dash); else ctx.setLineDash([]);
    ctx.beginPath();let started=false;
    s.data.forEach((v,i)=>{{
      if(v===null||!isFinite(v))return;
      const x=pad.l+cw*(i/(n-1)),y=pad.t+ch*(1-(v-mn)/rng);
      started?(ctx.lineTo(x,y)):(ctx.moveTo(x,y),started=true);
    }});
    ctx.stroke();ctx.setLineDash([]);
  }});
  seriesList.filter(s=>!s.noLegend).forEach((s,i)=>{{
    const lx=pad.l+i*140,ly=6;
    ctx.fillStyle=s.color;ctx.fillRect(lx,ly,16,3);
    ctx.fillStyle="#94a3b8";ctx.font="10px Inter,system-ui";ctx.textAlign="left";
    ctx.fillText(s.name,lx+20,ly+4);
  }});
}}

const months=DATA.monthly.map(m=>m.month);
const ivCapped=DATA.monthly.map(m=>Math.min(m.med_iv,60));
lineChart("monthIV",340,200,
  [{{data:ivCapped,labels:months,color:"#38bdf8",name:"Median IV %"}}],
  {{min:0,max:60}});
lineChart("monthIVRV",340,200,[
  {{data:DATA.monthly.map(m=>m.med_ivrv),labels:months,color:"#a78bfa",name:"Median IV/RV"}},
  {{data:DATA.monthly.map(()=>1.0),labels:months,color:"#4ade80",name:"1.0x",lw:1,dash:[4,4]}}
],{{min:0.5,max:4}});
lineChart("monthPct",340,200,
  [{{data:DATA.monthly.map(m=>m.pct_ivrv_lt1),labels:months,color:"#4ade80",name:"% IV/RV<1.0"}}],
  {{min:0,max:50}});

// ── Time-of-day dual axis ───────────────────────────────────────────────────
(function(){{
  const {{ctx,w,h}}=setup("tod",460,220);
  const pad={{t:24,r:52,b:28,l:40}};
  const cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;
  const tod=DATA.tod,n=tod.length;
  const mn1=0.5,mx1=3.0,mn2=0,mx2=25;

  ctx.strokeStyle="#1e2a3a";ctx.lineWidth=1;
  for(let i=0;i<=5;i++){{
    const y=pad.t+ch*(1-i/5);
    ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(pad.l+cw,y);ctx.stroke();
    ctx.fillStyle="#64748b";ctx.font="10px Inter,system-ui";
    ctx.textAlign="right";
    ctx.fillText((mn1+(mx1-mn1)*(i/5)).toFixed(1)+"x",pad.l-4,y+3);
    ctx.textAlign="left";
    ctx.fillText((mn2+(mx2-mn2)*(i/5)).toFixed(0)+"%",pad.l+cw+4,y+3);
  }}
  tod.forEach((d,i)=>{{
    if(i%2===0){{
      const x=pad.l+cw*(i/(n-1));
      ctx.fillStyle="#64748b";ctx.font="9px Inter,system-ui";ctx.textAlign="center";
      ctx.fillText(d.time,x,h-pad.b+11);
    }}
  }});

  function dl(vals,mn,mx,col,lw){{
    ctx.strokeStyle=col;ctx.lineWidth=lw||2;ctx.beginPath();
    vals.forEach((v,i)=>{{
      const x=pad.l+cw*(i/(n-1)),y=pad.t+ch*(1-(v-mn)/(mx-mn));
      i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
    }});ctx.stroke();
  }}
  dl(tod.map(d=>d.med_ivrv),mn1,mx1,"#a78bfa",2);
  dl(tod.map(d=>d.pct_ivrv_lt1),mn2,mx2,"#4ade80",2);

  ctx.fillStyle="#a78bfa";ctx.fillRect(pad.l,6,16,3);
  ctx.fillStyle="#94a3b8";ctx.font="10px Inter,system-ui";ctx.textAlign="left";
  ctx.fillText("Med IV/RV (left)",pad.l+20,10);
  ctx.fillStyle="#4ade80";ctx.fillRect(pad.l+150,6,16,3);
  ctx.fillStyle="#94a3b8";ctx.fillText("% IV/RV<1 (right)",pad.l+170,10);
}})();

// ── Scatter ─────────────────────────────────────────────────────────────────
(function(){{
  const {{ctx,w,h}}=setup("scatter",460,220);
  const pad={{t:14,r:16,b:28,l:40}};
  const cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;
  const mx=55;

  ctx.strokeStyle="#1e2a3a";ctx.lineWidth=1;
  for(let i=0;i<=5;i++){{
    const y=pad.t+ch*(1-i/5),x=pad.l+cw*(i/5),v=mx*(i/5);
    ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(pad.l+cw,y);ctx.stroke();
    ctx.beginPath();ctx.moveTo(x,pad.t);ctx.lineTo(x,pad.t+ch);ctx.stroke();
    ctx.fillStyle="#64748b";ctx.font="10px Inter,system-ui";
    ctx.textAlign="right";ctx.fillText(v.toFixed(0)+"%",pad.l-4,y+3);
    ctx.textAlign="center";ctx.fillText(v.toFixed(0)+"%",x,h-pad.b+11);
  }}
  ctx.strokeStyle="#334155";ctx.lineWidth=1;ctx.setLineDash([4,4]);
  ctx.beginPath();ctx.moveTo(pad.l,pad.t+ch);ctx.lineTo(pad.l+cw,pad.t);ctx.stroke();
  ctx.setLineDash([]);

  DATA.scatter.forEach(p=>{{
    const x=pad.l+cw*(p.iv/mx),y=pad.t+ch*(1-p.rv/mx);
    const ratio=p.iv/(p.rv||0.01);
    ctx.fillStyle=ratio<1?"rgba(74,222,128,0.75)":ratio>1.5?"rgba(239,68,68,0.55)":"rgba(100,116,139,0.55)";
    ctx.beginPath();ctx.arc(x,y,2.5,0,Math.PI*2);ctx.fill();
  }});

  ctx.fillStyle="#64748b";ctx.font="11px Inter,system-ui";
  ctx.textAlign="center";ctx.fillText("IV %",pad.l+cw/2,h-2);
  ctx.save();ctx.translate(10,pad.t+ch/2);ctx.rotate(-Math.PI/2);
  ctx.textAlign="center";ctx.fillText("RV30 %",0,0);ctx.restore();
}})();
</script>
</body>
</html>"""

OUT_PATH.write_text(html)
print(f"Dashboard written -> {OUT_PATH}")

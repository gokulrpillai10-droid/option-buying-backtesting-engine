"""Elite-quant deployability review of Strategy A (ORB call breakout).

Per-trade return basis = net / premium-deployed (capital-agnostic, since option
buying ties up exactly the premium). Sharpe annualised from per-trade stats and
cross-checked on a monthly P&L series. Includes tail-dependence, significance,
drawdown, streaks, and a 10k-path Monte Carlo bootstrap.
"""
import numpy as np
import pandas as pd

t = pd.read_csv("analysis/combined_trades.csv", parse_dates=["date"])
a = t[t.strat == "A"].sort_values("date").reset_index(drop=True)
a["r"] = a["net"] / a["premium"]                 # return on premium deployed
n = len(a)
years = (a.date.max() - a.date.min()).days / 365.25
tpy = n / years
net = a.net.sum()

# ---- return / payoff ----
wins, losses = a[a.net > 0], a[a.net <= 0]
win_rate = len(wins) / n
avg_win, avg_loss = wins.net.mean(), losses.net.mean()
payoff = avg_win / abs(avg_loss)
pf = wins.net.sum() / -losses.net.sum()
expectancy_r = a.r.mean()                        # mean return per trade
exp_rupee = a.net.mean()

# ---- risk-adjusted ----
sharpe_tr = a.r.mean() / a.r.std()
sharpe_ann = sharpe_tr * np.sqrt(tpy)
downside = a.r[a.r < 0].std()
sortino_ann = (a.r.mean() / downside) * np.sqrt(tpy)

# ---- equity / drawdown (rupees) ----
eq = a.net.cumsum()
peak = eq.cummax()
dd = peak - eq
maxdd = dd.max()
# drawdown duration in trades
underwater = dd > 0
mdd_dur = 0; cur = 0
for u in underwater:
    cur = cur + 1 if u else 0
    mdd_dur = max(mdd_dur, cur)

# ---- monthly series for calendar Sharpe + % positive months ----
a["ym"] = a.date.dt.to_period("M")
monthly = a.groupby("ym").net.sum()
CAP = 50000.0                                     # notional 1-lot account assumption
m_ret = monthly / CAP
sharpe_m = m_ret.mean() / m_ret.std() * np.sqrt(12)
pos_months = (monthly > 0).mean()
cagr = (1 + net / CAP) ** (1 / years) - 1
calmar = (net / years) / maxdd

# ---- streaks ----
def max_streak(mask):
    best = cur = 0
    for x in mask:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best
max_loss_streak = max_streak((a.net <= 0).values)
max_win_streak = max_streak((a.net > 0).values)

# ---- tail dependence ----
top = a.sort_values("net", ascending=False)
net_ex_top5 = net - top.head(5).net.sum()
net_ex_top10 = net - top.head(10).net.sum()
top10_share = top.head(10).net[top.head(10).net > 0].sum() / wins.net.sum()

# ---- statistical significance (per-trade mean return) ----
tstat = a.r.mean() / (a.r.std() / np.sqrt(n))

# ---- Monte Carlo bootstrap (resample trade sequence) ----
rng = np.random.default_rng(7)
finals, maxdds = [], []
arr = a.net.values
for _ in range(10000):
    s = rng.choice(arr, size=n, replace=True)
    e = np.cumsum(s)
    finals.append(e[-1])
    maxdds.append((np.maximum.accumulate(e) - e).max())
finals = np.array(finals); maxdds = np.array(maxdds)

print("=" * 70)
print("  STRATEGY A — ORB CALL BREAKOUT : QUANT DEPLOYABILITY REVIEW")
print("=" * 70)
print(f"Trades {n} over {years:.1f}y  ({tpy:.0f}/yr) | {a.date.min().date()} -> {a.date.max().date()}")
print(f"Median premium/trade Rs {a.premium.median():,.0f}  (assumed 1-lot acct cap Rs {CAP:,.0f})")
print("-" * 70)
print("RETURN & PAYOFF")
print(f"  Net P&L            Rs {net:,.0f}   (gross {a.gross.sum():,.0f}, costs {a.cost.sum():,.0f} = {a.cost.sum()/a.gross.sum()*100:.0f}% of gross)")
print(f"  Expectancy         Rs {exp_rupee:,.0f}/trade   ({expectancy_r*100:+.2f}% of premium)")
print(f"  Win rate           {win_rate:.1%}   payoff (win/loss) {payoff:.2f}   profit factor {pf:.2f}")
print(f"  Avg win Rs {avg_win:,.0f} | avg loss Rs {avg_loss:,.0f}")
print("-" * 70)
print("RISK-ADJUSTED")
print(f"  Sharpe (annualised, per-trade) {sharpe_ann:.2f}")
print(f"  Sharpe (annualised, monthly)   {sharpe_m:.2f}")
print(f"  Sortino (annualised)           {sortino_ann:.2f}")
print(f"  CAGR {cagr*100:.1f}%  |  Calmar {calmar:.2f}")
print("-" * 70)
print("DRAWDOWN & STREAKS")
print(f"  Max drawdown       Rs {maxdd:,.0f}   ({maxdd/CAP*100:.0f}% of {CAP:,.0f} acct)")
print(f"  Max DD duration    {mdd_dur} trades")
print(f"  Longest losing streak {max_loss_streak} | winning streak {max_win_streak}")
print(f"  % profitable months  {pos_months:.0%}")
print("-" * 70)
print("TAIL DEPENDENCE  (is the edge a few lucky trades?)")
print(f"  Net Rs {net:,.0f}  ->  excl top-5 winners Rs {net_ex_top5:,.0f}  ->  excl top-10 Rs {net_ex_top10:,.0f}")
print(f"  Top-10 winners = {top10_share:.0%} of all gross winnings")
print("-" * 70)
print("STATISTICAL SIGNIFICANCE")
print(f"  t-stat of mean per-trade return = {tstat:.2f}   (>2 ~ 95% confidence)")
print("-" * 70)
print("MONTE CARLO (10k bootstraps of the trade sequence)")
print(f"  Final P&L  median Rs {np.median(finals):,.0f} | 5th pct Rs {np.percentile(finals,5):,.0f} | P(profit) {np.mean(finals>0):.0%}")
print(f"  Max DD     median Rs {np.median(maxdds):,.0f} | 95th pct Rs {np.percentile(maxdds,95):,.0f}")
print("=" * 70)

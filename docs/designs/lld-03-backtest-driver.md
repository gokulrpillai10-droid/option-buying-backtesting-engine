# LLD-03 — Backtest Driver (Options)

> Module: `engine/backtest.py` (`Backtest`, `Knobs`, `Result`).
> Exit value types → [LLD-04](./lld-04-exit-mechanics.md); positions & costs →
> [LLD-05](./lld-05-position-costs-metrics.md).
> Parent: [High-Level Design](./high-level-design.md).

The driver is the single loop that runs every strategy shape — naked leg, spread,
scale-out, intrabar stop / target / trail — through the same proven path. This is
where the fill contract, liquidity floor, slippage, and exit resolution live.

## 1. Top-level flow

```mermaid
flowchart TB
    RUN["Backtest.run(strategy, start, end)"] --> STREAM["MarketData.stream(start, end)"]
    STREAM --> EXP{"for each (expiry, {day: Chain})"}
    EXP --> DAY{"for each day (sorted)"}
    DAY --> RD["_run_day(strategy, day, chain)"]
    RD --> ACC["result.trades.extend(...)"]
    ACC --> DAY
    DAY --> EXP
    EXP --> RES["return Result"]
```

`run` is thin: it drives `MarketData.stream` (one parquet load per expiry) and
delegates each day to `_run_day`. All the mechanics are in `_run_day`.

## 2. `_run_day` — the per-day state machine

```mermaid
flowchart TB
    START["bars = spot timestamps for this day"] --> DS["strategy.on_day_start(ctx@bar0)"]
    DS --> LOOP{"for each spot bar B"}
    LOOP --> FLAT{"pos is None?"}

    FLAT -->|yes| ONBAR["orders = strategy.on_bar(ctx)"]
    ONBAR --> HASORD{"orders?"}
    HASORD -->|no| LOOP
    HASORD -->|"multi-leg or any sell"| SPREAD["_open_spread → SpreadPosition"]
    HASORD -->|"single buy"| Q["quote at B + timeframe"]
    Q --> VOL{"_volume_ok?<br/>(liquidity floor)"}
    VOL -->|no| LOOP
    VOL -->|yes| OPEN["Position at px*(1+eff_slip)<br/>peak = raw fill"]
    OPEN --> LOOP
    SPREAD --> LOOP

    FLAT -->|no| MANAGE["ex = strategy.manage(ctx, pos)"]
    MANAGE --> DISP{"dispatch on ex.kind"}
    DISP --> LOOP

    LOOP --> EOD["EOD square-off at day's last option bar"]
    EOD --> RET["return trades"]
```

Single position at a time. After a full exit the loop keeps scanning, so a
strategy may trade more than once a day.

## 3. The fill contract (anti-look-ahead)

> A signal on bar `B` fills at **`B + timeframe`** — the next bar's option open by
> default. Enforced structurally, not by convention.

```mermaid
sequenceDiagram
    participant S as Strategy
    participant BT as Backtest
    participant CH as Chain
    S->>BT: on_bar(ctx@B) → [Order]
    BT->>BT: fill_dt = B + timeframe
    BT->>CH: snapshot_at(fill_dt).call/put(strike)
    CH-->>BT: OptionQuote
    BT->>BT: px = q.open (or q.close if Knobs.fill_price='close')
    BT->>BT: entry = px * (1 + eff_slip)
```

`Knobs.fill_price` selects which price at `fill_dt`:

- `'open'` — the principled next-open (default).
- `'close'` — matches the legacy G12 scripts' convention, used only for
  reconciliation.

If the quote at `fill_dt` is missing, no position opens (the signal is dropped
that bar).

## 4. Entry paths

### 4.1 Single leg

`orders[0]` with `action="buy"` and one leg:

1. `q = _opt_quote(chain, fill_dt, strike, side)` — `None` → skip.
2. `qty = orders[0].qty or lots * lot_size_for(day)`.
3. Liquidity floor: `_volume_ok(...)` — signal-bar option volume must be
   `>= min_volume_mult * qty`, else skip (no re-entry; the strategy already
   marked the day entered).
4. `Position(side, strike, px*(1+eff_slip), qty)`; `peak = px` (raw fill seeds
   the trail peak).

### 4.2 Spread (multi-leg or any sell)

Routed to `_open_spread` when `len(orders) > 1` **or** any `action == "sell"`:

```mermaid
flowchart TB
    A["orders"] --> B["net[side] += qty (buy) / -qty (sell)"]
    B --> C{"any side net < 0?"}
    C -->|yes| RAISE["raise ValueError:<br/>naked short not modeled"]
    C -->|no| D{"for each leg"}
    D --> E["price at fill_dt"]
    E -->|None| ABORT["return None (all-or-none)"]
    E --> F["entry = px*(1+slip) buy / px*(1-slip) sell"]
    F --> D
    D --> G["SpreadPosition(legs)"]
```

**Naked-short guard.** Per option side, sold qty must not exceed bought qty.
Covered verticals are fine; NET-short exposure (unbounded loss, margin,
assignment) is refused loudly instead of being priced wrong. Spread fills are
all-or-none: if any leg can't be priced the whole entry aborts.

## 5. Liquidity floor — `_volume_ok`

```mermaid
flowchart TB
    A["min_volume_mult"] --> Z{"<= 0?"}
    Z -->|yes| OK["True (floor off)"]
    Z -->|no| S["snapshot_at(signal_dt)"]
    S --> T{"strike in snapshot?"}
    T -->|no| REJ["False"]
    T -->|yes| V["vol = quote.volume if is_traded else 0"]
    V --> C{"vol >= mult * qty?"}
    C -->|yes| OK
    C -->|no| REJ
```

The floor is judged on **signal-minute** volume (mirrors the validated fill
audit). Not-traded or missing strike → volume 0 → reject. The validated floor is
**10×**: it removes net-losing thin trades rather than hurting the edge.
`mult <= 0` disables it.

## 6. Exit dispatch — `manage` result kinds

While in a single-leg position, `manage(ctx, pos)` returns an `Exit` and the
driver dispatches on `ex.kind`:

```mermaid
flowchart TB
    EX["ex = manage(ctx, pos)"] --> K{"ex.kind"}
    K -->|market| M["fill at B+timeframe open<br/>_book(fraction)"]
    K -->|level| L["walk 1-min option bars in [B, B+tf)<br/>resolve_level_exit → gap-through fill"]
    K -->|spot_level| SL["walk 1-min SPOT bars<br/>cross → option fills NEXT 1-min open"]
    K -->|bracket| B["walk option bars<br/>stop-first (or target if optimistic)<br/>partial → consume, other side rests"]
    K -->|trail| T["track premium peak intrabar<br/>exit when low <= peak - gap (gap-through)"]
    K -->|two_stage| TS["engine-owned: bracket → on scale-out,<br/>hand off to trail in the SAME window"]
```

Each kind and its intrabar math is specified in [LLD-04](./lld-04-exit-mechanics.md).
Two mechanics worth stating at the driver level:

- **Level / bracket / trail / two_stage** resolve against the option's **1-min
  high/low** via `_opt_window(chain, B, B+timeframe, ...)` — so overshoot that a
  close-only sampler would miss is modeled.
- **`spot_level`** resolves the crossing on 1-min **spot** bars, then fills the
  option at the **next 1-min open** — one minute of honest latency.

Spread positions accept only `Exit.market` (fill each leg at `B+timeframe`,
all-or-none) — resting/intrabar exits are single-leg only.

## 7. Partial scale-outs — `_book`

```mermaid
flowchart TB
    A["_book(pos, fill, reason, fraction, slip, ...)"] --> F{"fraction >= 1.0?"}
    F -->|yes| FULL["pos.close(fill*(1-slip)) → Trade<br/>return None (flat)"]
    F -->|no| PART["close_qty = pos.qty * fraction<br/>book partial Trade<br/>return Position(qty - close_qty)"]
```

`fraction < 1.0` books a partial and returns the remaining `Position` to ride —
partial scale-out is a first-class engine feature. `two_stage` and a partial
`bracket` both loop `_book` within one window: after a partial fill the other
side keeps resting via `ex.consume(reason)`.

## 8. Intrabar ambiguity — the `intrabar` knob

Inside a single 1-min bar the true order of events is unknowable. `Knobs.intrabar`
bounds it, and a GO verdict should survive the pessimistic corner:

```mermaid
flowchart LR
    subgraph mixed["mixed (default, historical)"]
        A1["trail: fold bar high into peak BEFORE low check (optimistic)"]
        A2["bracket: check stop BEFORE target (pessimistic)"]
    end
    subgraph optimistic
        B1["bracket: target wins the tie"]
    end
    subgraph pessimistic
        C1["trail: check low against PRIOR bar's peak first"]
    end
```

| Kind | `mixed` | `optimistic` | `pessimistic` |
|---|---|---|---|
| `bracket` | stop-first | target wins tie | stop-first |
| `trail` | high→peak then low check | same as mixed | low checked vs prior peak first |

Run both extremes; read the spread as the error band. The driver validates
`intrabar ∈ {mixed, optimistic, pessimistic}` at construction.

## 9. Effective slippage — `_eff_slip`

```mermaid
flowchart TB
    S0["s = slippage_pct (flat base)"] --> R{"slippage_range_k > 0?"}
    R -->|yes| RANGE["s += k * (high - low) / mid<br/>(fast bars cost more)"]
    R -->|no| KEEP
    RANGE --> KEEP["s"]
    KEEP --> TH{"thin_exit_mult>1 AND floor on<br/>AND fill-bar vol < mult*qty?"}
    TH -->|yes| MUL["s *= thin_exit_mult"]
    TH -->|no| DONE
    MUL --> DONE["effective slip"]
```

Slippage is applied adversely to the **fill price** (not in the cost model):
`fill * (1 + slip)` on a buy, `fill * (1 - slip)` on a sell. The validated
realistic value is **0.25%** (justified by a liquidity audit — a 6-lot order is
~0.01% of the deep-ITM strike's median day-volume, so market impact ≈ 0). The two
extras (`slippage_range_k`, `thin_exit_mult`) default off, reproducing the flat
historical behavior.

## 10. EOD square-off

The engine is strictly intraday — nothing holds overnight.

```mermaid
flowchart TB
    A["day loop ends with pos open"] --> B{"SpreadPosition?"}
    B -->|yes| S["price each leg at day's last option bar<br/>close all-or-none, reason='eod'"]
    B -->|no| L["quote at day's last option bar<br/>close at q.close*(1-eff_slip), reason='eod'"]
```

## 11. `Knobs` — the sensitivity dials

```python
Knobs(slippage_pct=0.0, timeframe="5min", fill_price="open",
      min_volume_mult=0.0, intrabar="mixed",
      slippage_range_k=0.0, thin_exit_mult=1.0)
```

| Knob | Meaning | Validated / default |
|---|---|---|
| `slippage_pct` | flat adverse slip on every fill | 0.25% realistic |
| `timeframe` | signal-bar size (also the fill offset) | `"5min"` |
| `fill_price` | `'open'` (principled) or `'close'` (G12 legacy) | `'open'` |
| `min_volume_mult` | liquidity floor multiple; 0 = off | 10× validated |
| `intrabar` | `mixed` / `optimistic` / `pessimistic` | `mixed` |
| `slippage_range_k` | range-scaled friction; 0 = off | 0 |
| `thin_exit_mult` | penalty when exit bar can't absorb qty; 1 = off | 1 |

## 12. `Result` — metrics

```mermaid
classDiagram
    class Result {
        +trades list~Trade~
        +carry_forwards int
        +net() float
        +win_rate() float
        +profit_factor() float
        +tail_excl(n) float
        +by_year() dict
        +t_stat() float
    }
```

| Metric | Definition | Reads as |
|---|---|---|
| `net()` | Σ `t.net` | rupee P&L |
| `win_rate()` | share of trades with `net > 0` | hit rate |
| `profit_factor()` | Σ wins / Σ losses | > 1 = profitable; `inf` if no losses |
| `tail_excl(n)` | net with the top-`n` winners removed | tail-dependence check |
| `by_year()` | `{year: net}` | is every year green? |
| `t_stat()` | t-stat of mean per-trade return on premium deployed | significance |
| `carry_forwards` | option-price gaps carried forward | data-quality counter |

`t_stat` normalizes each trade's P&L by capital deployed (`entry * qty`) so it
measures return significance, not rupee size. Needs ≥ 2 trades.

## 13. Appendix — the legacy `Runner`

`engine/runner.py` + `engine/execution/model.py` + `engine/position/exits.py` are
an **earlier, simpler** path that predates the `Backtest` driver:

```mermaid
flowchart LR
    R["Runner.run"] --> EM["ExecutionModel.enter<br/>(tradeability + liquidity → Fill @ t+1 open)"]
    EM --> BT["_book_trade: walk option bars"]
    BT --> RE["resolve_exit (hard stop / trailing / time stop)"]
    RE --> TR["Position.close → Trade"]
```

It supports one exit family (percentage hard-stop + trailing + time stop via
`resolve_exit`) and keeps `Rejection` records so orders are never silently
dropped. It is retained for the `test_runner_*` golden tests and simple studies;
new work uses `Backtest`, which subsumes it with richer exits, spreads, partials,
and the `Knobs` sweep. Both share `Position`, `CostModel`, and the fill-at-next
principle.

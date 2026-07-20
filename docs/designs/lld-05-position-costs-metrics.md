# LLD-05 — Positions, Costs & Metrics

> Modules: `engine/position/manager.py` (`Position`, `SpreadPosition`,
> `SpreadLeg`, `Trade`), `engine/execution/costs.py` (`CostModel`),
> `Result` (in `engine/backtest.py`, see [LLD-03](./lld-03-backtest-driver.md) §12).
> Parent: [High-Level Design](./high-level-design.md).

This is the accounting layer: how an open holding becomes a fully-costed `Trade`,
how India F&O transaction costs are computed, and how trades roll up into metrics.

## 1. Object model

```mermaid
classDiagram
    class Position {
        side: str
        strike: int
        entry: float
        qty: int
        +close(exit, cost_model, n_orders, reason, qty) Trade
    }
    class SpreadPosition {
        legs: tuple~SpreadLeg~
        +debit() float
        +unrealized(prices) float
        +close(exit_prices, cost_model, slip, reason) Trade
    }
    class SpreadLeg {
        action: str  buy|sell
        side: str    CALL|PUT
        strike: int
        entry: float
        qty: int
    }
    class Trade {
        side, strike, qty
        entry, exit
        gross, cost, net
        reason
        date, entry_dt, exit_dt
    }
    class CostModel {
        brokerage_per_order, stt_sell
        exchange, sebi, stamp_buy, gst
        +total(buy_value, sell_value, n_orders) float
    }
    Position ..> Trade : close() →
    SpreadPosition ..> Trade : close() →
    SpreadPosition o-- SpreadLeg
    Position ..> CostModel : uses
    SpreadPosition ..> CostModel : uses
```

`Trade` is a frozen, instrument-agnostic dataclass. The same type is reused by
the equity track (`side` = LONG/SHORT, `strike` = 0), so all `Result` metrics and
tear-sheet code work unchanged across options and equities.

## 2. `Position` — long-premium holding

A `Position` is a long-premium option holding. Buying either a call or a put
profits when *its own* premium rises, so P&L is the same formula for both:

```
gross = (exit - entry) * qty
```

```mermaid
flowchart TB
    A["Position.close(exit, cost_model, n_orders=2, reason, qty=None)"] --> Q["q = qty or self.qty"]
    Q --> G["gross = (exit - entry) * q"]
    G --> C["cost = cost_model.total(entry*q, exit*q, n_orders)"]
    C --> T["Trade(gross, cost, net=gross-cost, ...)"]
```

**Partial closes**: pass a smaller `qty`; the caller (`Backtest._book`) books that
fraction and keeps a fresh `Position` for the remainder. This is what powers
scale-outs. `n_orders` defaults to 2 (one buy + one sell round trip).

## 3. `SpreadPosition` / `SpreadLeg` — multi-leg verticals

A `SpreadPosition` is a set of legs. Combined P&L is the sum of per-leg P&L, where
a BUY leg profits on a premium rise and a SELL leg on a fall:

```mermaid
flowchart TB
    subgraph "close(exit_prices, cost_model, slip, reason)"
        L{"for each leg, price"} --> BUY["buy leg: xp = p*(1-slip)<br/>gross += (xp - entry)*qty<br/>cost += total(entry*qty, xp*qty, 2)"]
        L --> SELL["sell leg: xp = p*(1+slip)<br/>gross += (entry - xp)*qty<br/>cost += total(xp*qty, entry*qty, 2)"]
    end
    BUY --> AGG["combined Trade (side='SPREAD')"]
    SELL --> AGG
```

- `debit()` — net premium paid to open (buy premium − sell premium received).
- `unrealized(prices)` — combined mark-to-market given current per-leg prices.
- `close(exit_prices, ...)` — closes all legs; a BUY leg sells-to-close DOWN, a
  SELL leg buys-to-close UP, both adverse. Each leg is costed independently
  (2 orders each). The resulting `Trade` reports a per-unit `entry` = `debit/qty`
  so capital-deployed metrics stay meaningful.

This gives the engine first-class credit/debit spread support, so premium-selling
structures are testable on the same trusted engine — subject to the driver's
naked-short guard (LLD-03 §4.2): each side's sold qty must be covered by bought
qty.

## 4. `CostModel` — India F&O card

The single place transaction costs are computed, ported verbatim from validated
production code and golden-tested:

```mermaid
flowchart TB
    IN["total(buy_value, sell_value, n_orders)"] --> B["brokerage = 20 * n_orders (flat)"]
    IN --> S["stt = 0.1% * sell_value (SELL side)"]
    IN --> E["exch = 0.03503% * (buy+sell)"]
    IN --> SE["sebi = 0.0001% * (buy+sell)"]
    IN --> ST["stamp = 0.003% * buy_value (BUY side)"]
    B & E & SE --> G["gst = 18% * (brokerage + exch + sebi)"]
    B & S & E & SE & ST & G --> SUM["Σ = total round-trip cost"]
```

| Charge | Rate | Applied to |
|---|---|---|
| Brokerage | ₹20 / order (flat) | per executed order |
| STT | 0.1% | sell-side premium |
| Exchange txn | 0.03503% | both sides |
| SEBI | 0.0001% | both sides |
| Stamp | 0.003% | buy side |
| GST | 18% | on (brokerage + exchange + SEBI) |

All rates are configurable (frozen dataclass fields), so a **stress preset** can
dial them up for a friction sweep. **Slippage is deliberately not here** — it is
applied adversely to the fill price inside the driver (LLD-03 §9), keeping "market
impact" and "statutory charges" as separate, independently-stressable knobs.

The cash-equity track has its own independent `EquityCostModel` (percentage
brokerage with a cap, different STT / exchange rates) — see
[LLD-06](./lld-06-equity-subsystem.md).

## 5. `Trade` — the closed unit

```mermaid
classDiagram
    class Trade {
        side: str
        strike: int
        qty: int
        entry: float
        exit: float
        gross: float
        cost: float
        net: float
        reason: str
        date: date
        entry_dt: datetime
        exit_dt: datetime
    }
```

`net = gross - cost`. The `date`, `entry_dt`, and `exit_dt` timestamps are set by
the driver (via `dataclasses.replace`) after `close()` returns, since the
position object doesn't know the calendar context. `reason` carries the exit
cause (LLD-04 §7) for slicing results.

## 6. `Result` metrics recap

`Result` (specified in [LLD-03](./lld-03-backtest-driver.md) §12) aggregates a
list of `Trade`. The metrics chosen are the ones that catch a lying backtest:

```mermaid
flowchart LR
    T["trades"] --> NET["net / win_rate / profit_factor"]
    T --> TAIL["tail_excl(n): net minus top-n winners<br/>→ is the edge tail-dependent?"]
    T --> YR["by_year(): every year green?"]
    T --> TS["t_stat(): return-on-capital significance"]
```

- `tail_excl(n)` exposes edges that only exist because of a handful of monster
  winners.
- `by_year()` is the walk-forward eyeball — a real edge is green most years.
- `t_stat()` normalizes each trade by capital deployed (`entry * qty`), so a few
  large-notional trades can't masquerade as significance.

## 7. Design rationale

| Decision | Why |
|---|---|
| Single long-premium P&L formula | calls and puts both profit on own-premium rise |
| Partial close returns a fresh `Position` | clean scale-out without mutable state |
| Per-leg independent costing | spreads pay real per-order charges |
| Costs configurable + slippage separate | stress statutory vs. impact independently |
| One `Trade` type for options + equity | all metrics/tear-sheets reused unchanged |
| Naked-short refused at the driver | unbounded loss / margin / assignment unmodeled |

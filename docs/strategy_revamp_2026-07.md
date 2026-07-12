# Strategy Revamp — July 2026

Date: 2026-07-12

## Why the live BTC bot made no valuable trades

Replaying the exact `config/live_btc.env` that was deployed (5-minute BTC/USD,
$150 account) over 120 days of real Alpaca bars (2026-03-14 → 2026-07-12,
34,559 bars):

- **7 signals, 2 trades, both losses** (net `-$0.85`). The entry required ~14
  filters to pass simultaneously; individually the momentum filter blocked 90%
  of bars, regime 88%, pullback depth 84%, volume 78%. Their intersection was
  effectively never.
- Position size was capped at `~$45-52` (`TARGET_POSITION_NOTIONAL_PCT=0.30`
  of $150). Alpaca crypto costs ~0.25% taker fee + spread per side
  (~0.6% round trip), i.e. `~$0.28` per round trip — larger than the typical
  gross P&L of a 5-minute trade. Even the trades that did fire could not pay
  for themselves.
- Alpaca's BTC volume data is too thin for volume filters to be meaningful
  (many 5m bars print 0.0001 BTC).

So the bot was doubly dead: the filter stack meant it almost never traded, and
the sizing/fee math meant that when it did, a win was worth pennies.

## Why "more, smaller trades" makes it worse on crypto

We tested exactly that. Loosening the filters to a plain trend-following core
(SMA20>50, price>EMA55, 4h regime) on 30m bars over 12 months of real data
(2025-07 → 2026-07) with honest friction (0.3%/side):

- 184 trades, net `-$95` — **and ~$99 of that was friction.** Gross edge was
  roughly zero; the fees were the loss.
- Every "more active" variant tested (5m/15m/30m/60m trend, RSI mean-reversion
  grids) lost money after fees. On a $150 crypto account, activity itself is
  the cost.

Two structural facts made the year unwinnable for this bot:

1. **BTC fell ~46%** over the replay year (117.7k → 63.9k). Alpaca does not
   support shorting crypto, so a long-only bot had no tailwind to monetize.
2. Even in the **2024-25 bull leg** (Aug 2024 → Jul 2025, +37% in Nov alone), a
   strict-regime trend config netted only `+$3` after crypto fees. The fee
   schedule, not the signal, is the binding constraint at this account size.

## What changed

### 1. The live deployment switches to equities (QQQ)

Equities on Alpaca are commission-free; round-trip friction on QQQ is
~0.02-0.05% vs ~0.6% on BTC — ~20-30x cheaper. Over the same 12 months that
BTC fell 46%, QQQ rose ~30%.

New `config/live_spy.env` (`SYMBOL=QQQ`): hourly trend-following, long-only,
fractional, ~90% notional per position, daily-EMA(20) regime gate, wide
trailing exits, multi-day holds.

Replay evidence (2023-08 → 2026-07, ~2.9 years of hourly bars, $150 start,
$0.02/share slippage):

| Metric | QQQ | SPY (same config) |
|---|---|---|
| Net P&L | **+$55.3 (+37%)** | +$19.8 (+13%) |
| Profit factor | 1.76 | 1.37 |
| Win rate | 45% | 48% |
| Avg win / avg loss | $3.87 / -$1.77 | $1.91 / -$1.26 |
| Max drawdown | 6.7% | 6.9% |
| Trades | 74 (~2/month, avg hold ~7 days) | 79 |

Positive in every calendar year (2023 +$3.8, 2024 +$4.0, 2025 +$23.9,
2026 +$20.2), and results barely move under 3x slippage stress. Cash-account
friendly: ~2 entries/week max and multi-day holds keep T+1 settlement and
good-faith-violation rules comfortable.

### 2. The BTC profile becomes defensive instead of dead

`config/live_btc.env` is now an hourly trend config that only trades when the
4h EMA(120) is rising ≥0.8% per 24h, with ~90% notional sizing and wide trails:

- 2025-26 bear replay: **0 trades, $150 fully preserved** (market -46%).
- 2024-25 bull replay: +$3, PF 1.14.

It is intentionally dormant in downtrends. Keep it on paper, or expect it to
wake up only in a genuine bull leg.

### 3. Bug fixes that the investigation surfaced

- **Replay/live parity** ([bot/trade_controls.py](../bot/trade_controls.py)):
  the live store resets the consecutive-loss counter on ET day rollover, but
  the replay harness never did — after `MAX_CONSECUTIVE_LOSSES` losses, every
  backtest silently stopped trading *forever*. All previous research
  understated trade counts and is unreliable.
- **History fetch** ([bot/broker_alpaca.py](../bot/broker_alpaca.py)):
  `get_recent_bars` always looked back 7 calendar days regardless of timeframe,
  so any config on hourly bars (or with a daily regime) could never warm up its
  indicators live. The lookback now scales with timeframe and asset session
  hours.
- **Profile precedence** ([bot/profile.py](../bot/profile.py)): market
  defaults (`SYMBOL`, overnight/flatten flags) used to clobber the profile env
  file, making it impossible to run QQQ or hold overnight via config. Profile
  env keys now win; market values only fill gaps.

### 4. Deployment default

`docker-compose.yml`, `deploy/ec2/deploy_remote.sh`, and the GitHub Actions
workflow now default to the equity profile (`spy` market). BTC services remain
available as `trade-btc` / `paper-btc` / `research-btc`.

## Realistic expectations

The replay suggests roughly +10-15%/year on $150 (i.e. ~$20-50/year) with
~7% drawdowns — not "lots of money" in absolute terms, because $150 is the
limiting factor, not the strategy. Doubling deposits does more for absolute
P&L than any parameter change. Backtests are not guarantees; run the paper
profile alongside live and compare fills.

## Reproducing the research

The replay harness used is `bot/research.py` (`run_replay`) driven over cached
Alpaca bars. Crypto bars are available without auth:
`https://data.alpaca.markets/v1beta3/crypto/us/bars`. Equity validation used
Yahoo Finance hourly bars (730-day limit). Friction models: $210-250/BTC per
side (~0.3%), $0.02-0.06/share for equities.

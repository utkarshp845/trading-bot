# Live Account Path For A $100 Starting Account

Date: 2026-04-14

References:
- `reports/research_latest.md`
- `README.md`
- `bot/strategy_ma.py`
- `bot/main.py`
- `bot/research.py`

## Executive Summary

If this project is going to graduate from paper to real money, the safest starting path is not the current all-day `SPY` setup.

Recommended first live profile:
- cash account
- fractional or notional-based orders
- long-only
- one position at a time
- morning-entry-only
- end-of-day flatten enabled
- low max trades per day

This is a survivability-first path. The goal of the first live deployment is not to maximize profit. The goal is to confirm that the bot can behave correctly with real fills, real slippage, and small-account constraints.

## Why $100 Changes The Design

Starting with about `$100` forces a different design than the current paper profile.

Main reasons:
- Whole-share `SPY` is a poor fit. One share is too large relative to account size.
- A small account should not assume margin or shorting.
- Fees, spread, and slippage matter more when position size is tiny.
- High turnover is less forgiving in a small account.
- A strategy that is only slightly negative or barely positive in replay is not good enough once live friction is introduced.

The current repo is still optimized more for paper research than for a tiny live account.

## Recommended First Live Profile

Use this as the target shape for the first real-money pilot:
- Account type: cash account
- Direction: long-only
- Sizing: notional/fractional, not integer-share fixed size
- Position count: `1`
- Entry regime: morning only, preferably `0930-1130 ET`
- New entries after the morning window: disabled
- Exits: keep active for risk management and end-of-day flatten
- Overnight holding: disabled
- Max trades per day: low, ideally `1-3`
- Daily risk: strict and boring

Why this profile:
- It matches the better regime currently visible in replay.
- It avoids the weakest part of the current system: afternoon trading.
- It removes shorting, which a small starter account should not depend on.
- It matches the operational goal of learning safely instead of forcing scale too early.

## What Must Change In Code Before Live

Documentation only is being delivered here, but the code still needs a few concrete upgrades before a `$100` live account is reasonable.

Required changes:
- Fractional or notional order support in the broker layer
  - live orders should be sized by dollars or fractional quantity
  - integer-only quantity is not enough for a small `SPY` setup
- Long-only enforcement
  - both live and replay paths should be able to disable shorts explicitly
- Separate small-live config profile
  - do not reuse the same defaults as the broader paper profile
  - keep a dedicated small-live set of constraints
- Broker capability validation at startup
  - reject startup if the account cannot support the requested order mode
  - reject startup if config implies behavior that does not fit the account profile
- Replay support for the exact live profile
  - same symbol logic
  - same long-only restriction
  - same sizing mode
  - same session window

Without these changes, paper results and live behavior will still be too far apart.

## Suggested Rollout Stages

1. Re-run research with the proposed profile
- Morning-only entries
- Long-only
- Small-account sizing assumptions
- Same risk guardrails intended for live

2. Paper trade the exact small-live profile
- Do not paper trade a looser version and assume it carries over
- Use the same symbol, session window, and sizing mode intended for live

3. Start live with tiny notional risk
- Treat the first live period as validation, not scaling
- One position at a time
- Low daily trade count

4. Review operational behavior for several weeks
- Compare live fills vs replay assumptions
- Review slippage, missed trades, order states, and stale-bar behavior
- Confirm end-of-day flattening and pending-order handling are clean

5. Only then consider scaling above `$100`
- Increase only if the exact small-live profile is stable
- Do not add complexity and capital at the same time

## Risk Controls For The First Live Pilot

Keep the first live profile conservative:
- Per-trade notional cap
- Daily loss cap
- Max trades per day
- Stale-bar blocking enabled
- Pending-order blocking enabled
- No overnight positions
- One position at a time
- Morning-entry-only regime

These controls matter more than squeezing out extra trades. The first live goal is avoiding obvious failure modes while collecting trustworthy execution data.

## Success Criteria Before Increasing Capital

Do not scale the account until the following are true:
- The exact live profile shows positive expectancy in replay and walk-forward testing
- Live fills are reasonably close to replay slippage assumptions
- Order handling is stable
- No accidental short exposure or oversized positions occur
- No unexpected overnight positions occur
- The bot behaves predictably during weak or low-liquidity conditions

If those conditions are not met, keep the account tiny and continue iterating in research/paper mode.

## What Not To Do

- Do not go live with the current all-day version.
- Do not go live with shorting enabled on a tiny starter account.
- Do not assume paper fills will resemble live fills.
- Do not keep the current integer-share `SPY` assumptions and call that a `$100` profile.
- Do not scale capital before the small-account profile proves stable in both replay and live behavior.

## Bottom Line

The repo already has the beginnings of a useful trading framework, but the right path to real money is narrow and conservative.

For a `$100` start:
- simplify the strategy
- trade only the stronger morning regime
- remove shorts
- use fractional/notional sizing
- prove the exact live profile in paper first

That is the lowest-risk way to turn this from a paper system into a small live pilot without pretending the current all-day paper setup is already good enough.

# Build Log

Running log of strategy and infrastructure changes to this bot. Newest entry
first. Each entry should say what changed, why (with evidence where
possible), and what to watch after deploying it.

---

## 2026-07-12 — Repo alignment checkover

**What:** Swept the repo for leftover references to the old BTC-5m-default
setup after the strategy revamp below, since several docs/scripts still
assumed BTC was the primary deployed strategy and that bars were 5 minutes.

**Found and fixed:**
- Cron schedule (`deploy/ec2/install_cron.sh`) still defaulted to
  `*/5 * * * *`, i.e. every 5 minutes, but every profile now trades hourly
  bars (`TIMEFRAME_MINUTES=60` in all four `config/*.env` files). Left as-is,
  a fresh EC2 deploy would have invoked the bot ~12x per hour for one hourly
  decision — harmless (cooldown/pending-order guards prevent duplicate
  orders) but wasteful API calls and log noise. Changed the default to
  `5 * * * *` (once per hour, 5 minutes after each bar closes).
- `docs/github_actions_ec2.md` explicitly said "the deployed profile is BTC,
  24/7 scheduling is the correct default" — no longer true now that `spy`
  (QQQ) is the default deploy market. Updated.
- `OPERATIONS.md`: paper research equity doc said `$250`; both paper profiles
  now use `$150` to match the real account. Also removed a line claiming the
  BTC paper profile uses "larger sizing" than live — it's an exact mirror of
  `live_btc.env` now, by design, so paper fills validate the same strategy
  that runs live.
- `README.md`:
  - Opening description and Crypto section still framed BTC as the always-on
    24/7 default with 5-minute cron; corrected.
  - The "Deployment" section described AWS ECR, IAM roles, `run.sh`,
    `deploy.sh`, and pushing to a `main` branch — none of which exist in this
    repo. The actual deploy path is GitHub Actions → SSH/rsync → EC2 cron,
    already documented in `docs/github_actions_ec2.md`. Replaced the stale
    section with a pointer to that doc.
  - Removed the `REVERSAL_SIGNAL_STRENGTH_MIN` row from the risk-controls
    table — grepping `bot/` shows no code reads that variable (only test
    fixtures set it). It's vestigial from an earlier strategy iteration;
    flagged separately rather than touched here since removing it fully
    means editing `tests/test_research_replay.py`, which is out of scope for
    a docs pass.
- Added "superseded" banners to `docs/strategy_audit_current.md` and
  `docs/live_account_path_100usd.md` (both dated 2026-04-14, both centered on
  an intraday SPY strategy that no longer exists) pointing at
  `docs/strategy_revamp_2026-07.md`. Kept their original content intact as a
  historical record rather than rewriting them.

**Not changed:** `bot/main.py`'s hardcoded `SYMBOL` default of `SPY` and
`TIMEFRAME_MINUTES` default of `5` — these are fallbacks for running the bot
without a profile at all (e.g. `python -m bot.main` with a bare `.env`).
Every shipped profile overrides both, so this doesn't affect deployed
behavior, but it means an operator who skips the profile runner entirely
still gets the old defaults. Worth a follow-up if that path is ever used for
real.

**Verification:** `pytest` (66 passed), `bot.validate_runtime`,
`bot.validate_profile_env` for all four profiles — all still pass; this
entry was docs/config/deploy-script only, no strategy logic touched.

---

## 2026-07-12 — Strategy revamp: QQQ hourly trend replaces BTC 5m scalp

**What:** Investigated why the live BTC bot ($150 account, 5-minute bars) was
making no valuable trades, then replaced the live deployment strategy.

**Root cause (full writeup: `docs/strategy_revamp_2026-07.md`):**
- The live filter stack required ~14 conditions to align at once; replayed
  over 120 days of real bars it produced 2 trades, both losses.
- Position sizing capped trades at ~$45; Alpaca crypto's ~0.6% round-trip
  friction exceeds the gross P&L of most 5-minute trades, so even a trade
  that fired couldn't clear its own costs.
- BTC fell ~46% over the trailing year and Alpaca doesn't support shorting
  crypto, so a long-only bot had no tailwind.
- Directly tested the "more, smaller trades" hypothesis: a loosened
  high-frequency BTC config made 184 trades/year and lost $95, ~$99 of it
  pure fee friction. Activity was the cost, not the fix, on this venue/size.

**Changes:**
- `config/live_spy.env` + `config/paper_spy.env`: switched to `SYMBOL=QQQ`,
  hourly bars, long-only trend-following, ~90% notional fractional sizing,
  daily-EMA(20) regime gate, wide trailing exits, multi-day holds. Replay
  2023-08→2026-07 at $150 start: net +$55.3 (+37%), profit factor 1.76, win
  rate 45%, max drawdown 6.7%, positive every calendar year, stable under 3x
  slippage stress.
- `config/live_btc.env` + `config/paper_btc.env`: kept BTC live but made it
  defensive — hourly bars, strict 4h-EMA(120) uptrend gate
  (`REGIME_MIN_SLOPE_PCT=0.008`). Zero trades in the 2025-26 bear-year replay
  (capital preserved through a 46% market decline); mildly positive
  (+$3, PF 1.14) in the 2024-25 bull-year replay.
- `bot/trade_controls.py::sync_replay_day`: fixed the replay harness to reset
  `consecutive_losses` on ET day rollover, matching `bot/store.py`'s live
  behavior. Previously the replay never reset this counter, so any backtest
  that hit `MAX_CONSECUTIVE_LOSSES` stopped trading for the rest of the
  replay — every prior research report in this repo undercounted trades and
  is unreliable.
- `bot/broker_alpaca.py::get_recent_bars`: lookback window now scales with
  `timeframe_minutes` and asset session hours instead of a fixed 7 days.
  The old fixed window could never warm up hourly/daily-regime indicators
  for equities (only ~5 session-days per 7 calendar days).
- `bot/profile.py`: profile env file values now take precedence over market
  defaults (`SYMBOL`, `ALLOW_OVERNIGHT_HOLDING`, `FLATTEN_BEFORE_CLOSE_MINUTES`)
  instead of being silently overwritten by them — this is what let
  `config/live_spy.env` actually set `SYMBOL=QQQ` and hold overnight.
- `docker-compose.yml`, `deploy/ec2/deploy_remote.sh`,
  `.github/workflows/deploy-ec2.yml`: default deploy market switched from
  `btc` to `spy`. BTC remains reachable via `trade-btc` / `paper-btc` /
  `research-btc` compose services.
- Tests: updated profile-contract assertions in `tests/test_profile.py` to
  match the new defaults, added regression coverage for the loss-streak
  reset and profile-env precedence.

**Verification:** `pytest` (66 passed), `bot.validate_runtime`,
`bot.validate_profile_env` for all four profiles, and a replay of the exact
committed `config/live_spy.env` / `config/live_btc.env` files against real
historical bars (QQQ, SPY, BTC bull year, BTC bear year) to confirm the
numbers in `docs/strategy_revamp_2026-07.md` match what's actually deployed.

**What to watch after deploy:** run `docker compose run --rm paper` for a
week or two before trusting live fills; compare paper fills against the
replay assumptions (slippage, fill price) before increasing size.

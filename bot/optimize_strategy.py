from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from bot.broker_alpaca import get_historical_bars, make_clients
from bot.paths import APP_ROOT, REPORTS_DIR, ensure_runtime_dirs
from bot.research import build_strategy_config, run_replay, summarize_replay, walk_forward_splits


ET = ZoneInfo("America/New_York")

OPTIMIZED_KEYS = [
    "ALLOW_SHORTS",
    "SMA_FAST",
    "SMA_SLOW",
    "ADX_THRESHOLD",
    "LONG_ADX_THRESHOLD",
    "ATR_MAX_PCT",
    "LONG_ATR_MAX_PCT",
    "MIN_SMA_SPREAD_ATR_MULT",
    "MIN_VOLUME_RATIO",
    "TRAIL_ATR_MULTIPLIER",
    "TRAIL_AFTER_ATR_MULTIPLE",
    "MAX_BARS_IN_TRADE",
    "MAX_TRADES_PER_DAY",
    "COOLDOWN_BARS",
    "REGIME_ADX_MIN",
    "REGIME_MIN_SLOPE_PCT",
    "LONG_MIN_MOMENTUM_PCT",
    "MIN_ADX_DELTA",
    "PULLBACK_MIN_DEPTH_ATR",
    "PULLBACK_MAX_DEPTH_ATR",
    "REACCEL_MIN_BAR_BODY_ATR",
    "SPIKE_BAR_MAX_RANGE_ATR",
    "ENTRY_WINDOWS",
    "LONG_ENTRY_WINDOWS",
    "SHORT_ENTRY_WINDOWS",
]


@dataclass
class CandidateResult:
    params: dict[str, str]
    score: float
    full_summary: dict
    train_summary: dict
    test_summary: dict
    slippage_2x_summary: dict
    window_count: int
    positive_test_windows: int
    positive_train_windows: int
    acceptance_checks: dict[str, bool] | None = None
    accepted: bool = False


def _parse_grid(raw: str | None, fallback: list[str]) -> list[str]:
    if raw is None or not raw.strip():
        return fallback
    values = [token.strip() for token in raw.split(",") if token.strip()]
    return values or fallback


def _candidate_grid() -> dict[str, list[str]]:
    is_btc = os.getenv("SYMBOL", "").strip().upper() == "BTC/USD" or os.getenv("BOT_MARKET", "").strip().lower() == "btc"
    atr_defaults = ["0.015"] if is_btc else ["0.0030", "0.0035", "0.0045"]
    long_atr_defaults = ["0.012"] if is_btc else ["0.0025", "0.0030", "0.0035"]
    return {
        "ALLOW_SHORTS": ["false"],
        "SMA_FAST": _parse_grid(os.getenv("OPT_SMA_FAST_VALUES"), ["10", "20", "30"]),
        "SMA_SLOW": _parse_grid(os.getenv("OPT_SMA_SLOW_VALUES"), ["40", "50", "80"]),
        "ADX_THRESHOLD": _parse_grid(os.getenv("OPT_ADX_THRESHOLD_VALUES"), ["20", "25", "30"]),
        "LONG_ADX_THRESHOLD": _parse_grid(os.getenv("OPT_LONG_ADX_THRESHOLD_VALUES"), ["20", "22", "25"]),
        "ATR_MAX_PCT": _parse_grid(os.getenv("OPT_ATR_MAX_PCT_VALUES"), atr_defaults),
        "LONG_ATR_MAX_PCT": _parse_grid(os.getenv("OPT_LONG_ATR_MAX_PCT_VALUES"), long_atr_defaults),
        "MIN_SMA_SPREAD_ATR_MULT": _parse_grid(os.getenv("OPT_MIN_SMA_SPREAD_ATR_MULT_VALUES"), ["0", "0.1", "0.2"]),
        "MIN_VOLUME_RATIO": _parse_grid(os.getenv("OPT_MIN_VOLUME_RATIO_VALUES"), ["1.00", "1.05", "1.10"]),
        "TRAIL_ATR_MULTIPLIER": _parse_grid(os.getenv("OPT_TRAIL_ATR_MULTIPLIER_VALUES"), ["1.0", "1.5", "2.0"]),
        "TRAIL_AFTER_ATR_MULTIPLE": _parse_grid(os.getenv("OPT_TRAIL_AFTER_ATR_MULTIPLE_VALUES"), ["1.0", "1.5", "2.0"]),
        "MAX_BARS_IN_TRADE": _parse_grid(os.getenv("OPT_MAX_BARS_IN_TRADE_VALUES"), ["12", "18", "24"]),
        "MAX_TRADES_PER_DAY": _parse_grid(os.getenv("OPT_MAX_TRADES_PER_DAY_VALUES"), ["2", "3"]),
        "COOLDOWN_BARS": _parse_grid(os.getenv("OPT_COOLDOWN_BARS_VALUES"), ["4", "6", "8"]),
        "REGIME_ADX_MIN": _parse_grid(os.getenv("OPT_REGIME_ADX_MIN_VALUES"), ["18", "22"]),
        "REGIME_MIN_SLOPE_PCT": _parse_grid(os.getenv("OPT_REGIME_MIN_SLOPE_PCT_VALUES"), ["0.001", "0.0015", "0.002"]),
        "LONG_MIN_MOMENTUM_PCT": _parse_grid(os.getenv("OPT_LONG_MIN_MOMENTUM_PCT_VALUES"), ["0.0015", "0.002", "0.003"]),
        "MIN_ADX_DELTA": _parse_grid(os.getenv("OPT_MIN_ADX_DELTA_VALUES"), ["0", "0.1", "0.25"]),
        "PULLBACK_MIN_DEPTH_ATR": _parse_grid(os.getenv("OPT_PULLBACK_MIN_DEPTH_ATR_VALUES"), ["0.2", "0.3", "0.4"]),
        "PULLBACK_MAX_DEPTH_ATR": _parse_grid(os.getenv("OPT_PULLBACK_MAX_DEPTH_ATR_VALUES"), ["1.2", "1.5", "1.8"]),
        "REACCEL_MIN_BAR_BODY_ATR": _parse_grid(os.getenv("OPT_REACCEL_MIN_BAR_BODY_ATR_VALUES"), ["0.15", "0.20", "0.25"]),
        "SPIKE_BAR_MAX_RANGE_ATR": _parse_grid(os.getenv("OPT_SPIKE_BAR_MAX_RANGE_ATR_VALUES"), ["1.8", "2.0", "2.2"]),
        "ENTRY_WINDOWS": _parse_grid(os.getenv("OPT_ENTRY_WINDOWS_VALUES"), ["0000-2359"]),
    }


def iter_candidates() -> list[dict[str, str]]:
    grid = _candidate_grid()
    keys = list(grid.keys())
    candidates: list[dict[str, str]] = []
    max_candidates = int(os.getenv("OPT_MAX_CANDIDATES", "50"))

    total_combinations = 1
    for key in keys:
        total_combinations *= len(grid[key])
    sample_count = min(total_combinations, max_candidates * 4)
    if sample_count <= 0:
        return []

    if total_combinations <= sample_count:
        indexes = range(total_combinations)
    elif sample_count == 1:
        indexes = [0]
    else:
        indexes = sorted({round(i * (total_combinations - 1) / (sample_count - 1)) for i in range(sample_count)})

    for index in indexes:
        candidate: dict[str, str] = {}
        remainder = int(index)
        for key in reversed(keys):
            values = grid[key]
            candidate[key] = values[remainder % len(values)]
            remainder //= len(values)
        candidate = {key: candidate[key] for key in keys}
        if int(candidate["SMA_FAST"]) >= int(candidate["SMA_SLOW"]):
            continue
        if float(candidate["LONG_ATR_MAX_PCT"]) > float(candidate["ATR_MAX_PCT"]):
            continue
        if float(candidate["PULLBACK_MIN_DEPTH_ATR"]) >= float(candidate["PULLBACK_MAX_DEPTH_ATR"]):
            continue
        candidate["LONG_ENTRY_WINDOWS"] = candidate["ENTRY_WINDOWS"]
        candidate["SHORT_ENTRY_WINDOWS"] = candidate["ENTRY_WINDOWS"]
        candidates.append(candidate)
        if len(candidates) >= max_candidates:
            break

    return candidates[:max_candidates]


def _log(message: str) -> None:
    print(f"[optimize] {message}", flush=True)


@contextmanager
def temporary_env(overrides: dict[str, str]):
    previous = {key: os.getenv(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = str(value)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _aggregate_window_summaries(rows: list[dict]) -> dict:
    if not rows:
        return {
            "trade_count": 0,
            "net_pnl": 0.0,
            "profit_factor": None,
            "win_rate": None,
            "avg_pnl": None,
            "expectancy": None,
            "max_drawdown": None,
            "trades_per_day": 0.0,
            "window_count": 0,
            "positive_windows": 0,
            "median_window_net_pnl": None,
        }

    trade_count = sum(int(row["trade_count"]) for row in rows)
    gross_profit = sum(float(row.get("gross_profit") or 0.0) for row in rows)
    gross_loss_abs = sum(abs(float(row.get("gross_loss") or 0.0)) for row in rows)
    net_pnl = sum(float(row.get("net_pnl") or 0.0) for row in rows)
    trades_per_day = sum(float(row.get("trades_per_day") or 0.0) for row in rows) / len(rows)
    max_dd_values = [float(row["max_drawdown"]) for row in rows if row.get("max_drawdown") is not None]
    positive_windows = sum(1 for row in rows if float(row.get("net_pnl") or 0.0) > 0.0)
    median_window_net = median(float(row.get("net_pnl") or 0.0) for row in rows)

    return {
        "trade_count": trade_count,
        "net_pnl": net_pnl,
        "profit_factor": (gross_profit / gross_loss_abs) if gross_loss_abs > 0 else None,
        "win_rate": None,
        "avg_pnl": (net_pnl / trade_count) if trade_count > 0 else None,
        "expectancy": (net_pnl / trade_count) if trade_count > 0 else None,
        "max_drawdown": min(max_dd_values) if max_dd_values else None,
        "trades_per_day": trades_per_day,
        "window_count": len(rows),
        "positive_windows": positive_windows,
        "median_window_net_pnl": median_window_net,
    }


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def load_strategy_evidence(path: str | None = None) -> dict | None:
    configured = path or os.getenv("STRATEGY_EVIDENCE_PATH")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            REPORTS_DIR / "strategy_evidence_latest.json",
            APP_ROOT / "reports" / "strategy_evidence_latest.json",
        ]
    )

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.exists():
            continue
        try:
            return json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def _evidence_score_adjustment(evidence: dict | None, params: dict[str, str] | None) -> float:
    if not evidence or not params:
        return 0.0

    research = evidence.get("research") or {}
    worst_conditions = " ".join(str(item) for item in research.get("worst_conditions") or [])
    best_conditions = " ".join(str(item) for item in research.get("best_conditions") or [])
    adjustment = 0.0

    min_volume_ratio = _as_float(params.get("MIN_VOLUME_RATIO"))
    if "volume_ratio_bucket=0.8-1.0" in worst_conditions:
        adjustment += 6.0 if min_volume_ratio >= 1.0 else -6.0

    max_bars = _as_int(params.get("MAX_BARS_IN_TRADE"))
    if "hold_bucket=<30m" in worst_conditions:
        if "hold_bucket=60-120m" in best_conditions or "hold_bucket=120m+" in best_conditions:
            if max_bars >= 18:
                adjustment += 5.0
            elif max_bars <= 6:
                adjustment -= 8.0

    databases = evidence.get("databases") or []
    stale_count = sum(_as_int(db.get("stale_data_count")) for db in databases if isinstance(db, dict))
    if stale_count > 0:
        # Stale data is an operational issue, not an alpha source. Keep the
        # adjustment small so it nudges candidates but cannot rescue bad replay.
        adjustment -= min(5.0, stale_count * 0.5)

    return adjustment


def score_candidate(
    full_summary: dict,
    train_summary: dict,
    test_summary: dict,
    slippage_summary: dict | None = None,
    evidence: dict | None = None,
    params: dict[str, str] | None = None,
) -> float:
    trade_count = int(test_summary.get("trade_count") or 0)
    if trade_count <= 0:
        return -1_000_000.0

    trades_per_day = float(full_summary.get("trades_per_day") or 0.0)
    if trades_per_day < 0.5 or trades_per_day > 3.0:
        return -500_000.0 - abs(trades_per_day - 1.5) * 1000.0

    if slippage_summary is not None:
        slippage_trade_count = int(slippage_summary.get("trade_count") or 0)
        slippage_net = float(slippage_summary.get("net_pnl") or 0.0)
        slippage_expectancy = float(slippage_summary.get("expectancy") or 0.0)
        if slippage_trade_count <= 0 or slippage_net < 0.0 or slippage_expectancy < 0.0:
            return -400_000.0 + (slippage_net * 10.0)

    score = float(test_summary.get("net_pnl") or 0.0) * 10.0
    score += float(test_summary.get("positive_windows") or 0) * 8.0
    score += float(train_summary.get("positive_windows") or 0) * 2.0
    score += float(test_summary.get("profit_factor") or 0.0) * 20.0
    score += float(full_summary.get("profit_factor") or 0.0) * 5.0
    score += float(test_summary.get("median_window_net_pnl") or 0.0) * 5.0
    if slippage_summary is not None:
        score += float(slippage_summary.get("net_pnl") or 0.0) * 5.0
        score += float(slippage_summary.get("profit_factor") or 0.0) * 10.0

    test_drawdown = abs(min(0.0, float(test_summary.get("max_drawdown") or 0.0)))
    full_drawdown = abs(min(0.0, float(full_summary.get("max_drawdown") or 0.0)))
    score -= test_drawdown * 5000.0
    score -= full_drawdown * 1000.0

    train_net = float(train_summary.get("net_pnl") or 0.0)
    test_net = float(test_summary.get("net_pnl") or 0.0)
    score -= abs(train_net - test_net) * 0.5
    score -= abs(trades_per_day - 1.5) * 5.0
    score += _evidence_score_adjustment(evidence, params)
    return round(score, 6)


def _slippage_summary(
    bars: pd.DataFrame,
    cfg,
    sizing_mode: str,
    base_qty: int,
    starting_equity: float,
    multiplier: float,
) -> dict:
    stress_equity, stress_trades = run_replay(
        bars,
        cfg,
        sizing_mode,
        base_qty,
        starting_equity,
        slippage_multiplier=multiplier,
    )
    return summarize_replay(pd.DataFrame(stress_trades), stress_equity)


def evaluate_candidate(
    bars: pd.DataFrame,
    timeframe_minutes: int,
    sizing_mode: str,
    base_qty: int,
    starting_equity: float,
    train_days: int,
    test_days: int,
    params: dict[str, str],
    evidence: dict | None = None,
) -> CandidateResult:
    with temporary_env(params):
        cfg = build_strategy_config(timeframe_minutes)
        full_equity, full_trades = run_replay(bars, cfg, sizing_mode, base_qty, starting_equity)
        full_summary = summarize_replay(pd.DataFrame(full_trades), full_equity)
        slippage_2x_summary = _slippage_summary(
            bars,
            cfg,
            sizing_mode,
            base_qty,
            starting_equity,
            2.0,
        )

        train_windows: list[dict] = []
        test_windows: list[dict] = []
        for train_start, train_end, test_start, test_end in walk_forward_splits(bars.index, train_days, test_days):
            train_bars = bars[(bars.index >= train_start) & (bars.index < train_end)]
            test_bars = bars[(bars.index >= test_start) & (bars.index < test_end)]
            if train_bars.empty or test_bars.empty:
                continue

            train_equity, train_trades = run_replay(train_bars, cfg, sizing_mode, base_qty, starting_equity)
            train_summary = summarize_replay(pd.DataFrame(train_trades), train_equity)
            train_windows.append(train_summary)

            test_equity, test_trades = run_replay(test_bars, cfg, sizing_mode, base_qty, starting_equity)
            test_summary = summarize_replay(pd.DataFrame(test_trades), test_equity)
            test_windows.append(test_summary)

    aggregate_train = _aggregate_window_summaries(train_windows)
    aggregate_test = _aggregate_window_summaries(test_windows)
    score = score_candidate(
        full_summary,
        aggregate_train,
        aggregate_test,
        slippage_summary=slippage_2x_summary,
        evidence=evidence,
        params=params,
    )

    return CandidateResult(
        params=params,
        score=score,
        full_summary=full_summary,
        train_summary=aggregate_train,
        test_summary=aggregate_test,
        slippage_2x_summary=slippage_2x_summary,
        window_count=len(test_windows),
        positive_test_windows=int(aggregate_test.get("positive_windows") or 0),
        positive_train_windows=int(aggregate_train.get("positive_windows") or 0),
    )


def acceptance_checks(result: CandidateResult, baseline: CandidateResult | None = None) -> dict[str, bool]:
    min_pf = float(os.getenv("OPT_ACCEPT_MIN_PROFIT_FACTOR", "1.10"))
    min_trades_per_day = float(os.getenv("OPT_ACCEPT_MIN_TRADES_PER_DAY", "0.5"))
    max_trades_per_day = float(os.getenv("OPT_ACCEPT_MAX_TRADES_PER_DAY", "3.0"))
    min_positive_windows = int(os.getenv("OPT_ACCEPT_MIN_POSITIVE_TEST_WINDOWS", "2"))
    max_drawdown_abs = float(os.getenv("OPT_ACCEPT_MAX_DRAWDOWN_ABS", "0.10"))

    full_pf = float(result.full_summary.get("profit_factor") or 0.0)
    full_expectancy = float(result.full_summary.get("expectancy") or 0.0)
    trades_per_day = float(result.full_summary.get("trades_per_day") or 0.0)
    test_net = float(result.test_summary.get("net_pnl") or 0.0)
    full_drawdown = abs(min(0.0, float(result.full_summary.get("max_drawdown") or 0.0)))
    slippage_pf = float(result.slippage_2x_summary.get("profit_factor") or 0.0)

    checks = {
        "full_profit_factor_at_least_1_10": full_pf >= min_pf,
        "positive_expectancy": full_expectancy > 0.0,
        "trades_per_day_between_0_5_and_3": min_trades_per_day <= trades_per_day <= max_trades_per_day,
        "walk_forward_test_net_positive": test_net > 0.0,
        "at_least_two_positive_test_windows": result.positive_test_windows >= min_positive_windows,
        "two_x_slippage_profit_factor_at_least_1": slippage_pf >= 1.0,
        "max_drawdown_within_live_limit": full_drawdown <= max_drawdown_abs,
        "shorts_disabled": result.params.get("ALLOW_SHORTS", "false").strip().lower() == "false",
    }

    if baseline is not None:
        checks.update(
            {
                "score_beats_baseline": result.score > baseline.score,
                "expectancy_beats_baseline": full_expectancy > float(baseline.full_summary.get("expectancy") or 0.0),
                "test_net_beats_baseline": test_net > float(baseline.test_summary.get("net_pnl") or 0.0),
            }
        )

    return checks


def apply_acceptance(results: list[CandidateResult], baseline: CandidateResult | None = None) -> None:
    for result in results:
        checks = acceptance_checks(result, baseline)
        result.acceptance_checks = checks
        result.accepted = all(checks.values())


def _fmt_num(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _recommended_env_block(params: dict[str, str]) -> str:
    lines = [f"{key}={params[key]}" for key in OPTIMIZED_KEYS if key in params]
    return "\n".join(lines)


def _result_payload(result: CandidateResult) -> dict:
    return {
        "params": result.params,
        "score": result.score,
        "accepted": result.accepted,
        "acceptance_checks": result.acceptance_checks or {},
        "window_count": result.window_count,
        "positive_test_windows": result.positive_test_windows,
        "positive_train_windows": result.positive_train_windows,
        "full_summary": result.full_summary,
        "train_summary": result.train_summary,
        "test_summary": result.test_summary,
        "slippage_2x_summary": result.slippage_2x_summary,
        "recommended_env": _recommended_env_block(result.params),
    }


def write_report(path_md, path_json, payload: dict) -> None:
    lines = [
        "# Optimization Report",
        "",
        f"Generated: {datetime.now(timezone.utc).astimezone(ET)}",
        f"- Symbol: {payload['symbol']}",
        f"- Timeframe: {payload['timeframe_minutes']}m",
        f"- Sizing mode: {payload['sizing_mode']}",
        f"- Candidates evaluated: {payload['candidate_count']}",
        f"- Accepted candidates: {payload.get('accepted_candidate_count', 0)}",
        f"- Walk-forward windows: {payload['window_count']}",
        "",
        "## Baseline",
    ]

    baseline = payload.get("baseline")
    if baseline is None:
        lines.append("- Baseline was not evaluated.")
    else:
        lines.extend(
            [
                f"- Score: {_fmt_num(baseline['score'], 2)}",
                f"- Full net P&L: {_fmt_num(baseline['full_summary'].get('net_pnl'), 4)}",
                f"- Full profit factor: {_fmt_num(baseline['full_summary'].get('profit_factor'), 3)}",
                f"- Full expectancy: {_fmt_num(baseline['full_summary'].get('expectancy'), 4)}",
                f"- Trades/day: {_fmt_num(baseline['full_summary'].get('trades_per_day'), 3)}",
                f"- Test net P&L: {_fmt_num(baseline['test_summary'].get('net_pnl'), 4)}",
                f"- 2x slippage profit factor: {_fmt_num(baseline['slippage_2x_summary'].get('profit_factor'), 3)}",
            ]
        )

    lines.extend(
        [
            "",
        "## Best Candidate",
        ]
    )

    best = payload.get("best_candidate")
    if best is None:
        lines.append("- No candidates produced usable replay results.")
    else:
        lines.extend(
            [
                f"- Score: {_fmt_num(best['score'], 2)}",
                f"- Accepted: {best['accepted']}",
                f"- Test net P&L: {_fmt_num(best['test_summary'].get('net_pnl'), 4)}",
                f"- Test profit factor: {_fmt_num(best['test_summary'].get('profit_factor'), 3)}",
                f"- Test avg trade: {_fmt_num(best['test_summary'].get('avg_pnl'), 4)}",
                f"- Test max drawdown: {_fmt_pct(best['test_summary'].get('max_drawdown'))}",
                f"- Full trades/day: {_fmt_num(best['full_summary'].get('trades_per_day'), 3)}",
                f"- 2x slippage net P&L: {_fmt_num(best['slippage_2x_summary'].get('net_pnl'), 4)}",
                f"- 2x slippage profit factor: {_fmt_num(best['slippage_2x_summary'].get('profit_factor'), 3)}",
                f"- Positive test windows: {best['positive_test_windows']}/{best['window_count']}",
                "",
                "Acceptance checks:",
                *(f"- {name}: {passed}" for name, passed in best.get("acceptance_checks", {}).items()),
                "",
                "Recommended `.env` overrides:",
                "```env",
                best["recommended_env"],
                "```",
            ]
        )

    lines.extend(["", "## Top Candidates"])
    for idx, row in enumerate(payload.get("top_candidates", []), start=1):
        params = row["params"]
        lines.append(
            f"- #{idx} accepted={row['accepted']} score={_fmt_num(row['score'], 2)} test_net={_fmt_num(row['test_summary'].get('net_pnl'), 4)} "
            f"pf={_fmt_num(row['test_summary'].get('profit_factor'), 3)} "
            f"full_tpd={_fmt_num(row['full_summary'].get('trades_per_day'), 2)} "
            f"slip2x_net={_fmt_num(row['slippage_2x_summary'].get('net_pnl'), 4)} "
            f"slip2x_pf={_fmt_num(row['slippage_2x_summary'].get('profit_factor'), 3)} "
            f"wins={row['positive_test_windows']}/{row['window_count']} "
            f"sma={params['SMA_FAST']}/{params['SMA_SLOW']} "
            f"adx={params['ADX_THRESHOLD']}/{params['LONG_ADX_THRESHOLD']} "
            f"regime_adx={params['REGIME_ADX_MIN']} "
            f"regime_slope={params['REGIME_MIN_SLOPE_PCT']} "
            f"momentum={params['LONG_MIN_MOMENTUM_PCT']} "
            f"adx_delta={params['MIN_ADX_DELTA']} "
            f"pullback={params['PULLBACK_MIN_DEPTH_ATR']}-{params['PULLBACK_MAX_DEPTH_ATR']} "
            f"vol={params['MIN_VOLUME_RATIO']} "
            f"spike={params['SPIKE_BAR_MAX_RANGE_ATR']} "
            f"trail={params['TRAIL_ATR_MULTIPLIER']} "
            f"bars={params['MAX_BARS_IN_TRADE']} "
            f"cooldown={params['COOLDOWN_BARS']} "
            f"windows={params['ENTRY_WINDOWS']}"
        )

    lines.extend(
        [
            "",
            "## Selection Notes",
            "- Baseline is the currently loaded profile over the exact same bars and walk-forward windows.",
            "- Acceptance requires full-sample PF >= 1.10, positive expectancy, 0.5-3.0 trades/day, positive walk-forward test net, at least two positive test windows, 2x slippage PF >= 1.0, and improvement over baseline.",
            "- Candidates are heavily penalized if full-sample replay turns negative under 2x slippage.",
            "- If `strategy_evidence_latest.json` exists, ranking nudges candidates toward historically stronger pattern classes and away from known weak ones.",
            "- Ranking favors positive walk-forward test windows, positive test P&L, and profit factor above 1.0, while penalizing larger drawdowns and train/test gaps.",
            "- This report reduces naive curve-fitting, but it still does not guarantee future profitability.",
            "",
        ]
    )

    path_md.write_text("\n".join(lines), encoding="utf-8")
    path_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main() -> None:
    load_dotenv()
    ensure_runtime_dirs()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    symbol = os.getenv("SYMBOL", "SPY").strip().upper()
    timeframe_minutes = int(os.getenv("TIMEFRAME_MINUTES", "5"))
    sizing_mode = os.getenv("RESEARCH_SIZING_MODE", os.getenv("POSITION_SIZING_MODE", "fixed")).strip().lower() or "fixed"
    base_qty = int(os.getenv("QTY", "1"))
    lookback_days = int(os.getenv("RESEARCH_LOOKBACK_DAYS", "90"))
    train_days = int(os.getenv("RESEARCH_TRAIN_DAYS", "30"))
    test_days = int(os.getenv("RESEARCH_TEST_DAYS", "10"))
    starting_equity = float(os.getenv("RESEARCH_STARTING_EQUITY", "100000"))
    top_n = int(os.getenv("OPT_REPORT_TOP_N", "5"))
    progress_every = max(1, int(os.getenv("OPT_PROGRESS_EVERY", "5")))
    evidence = load_strategy_evidence()

    _log(
        f"starting symbol={symbol} timeframe={timeframe_minutes}m lookback_days={lookback_days} "
        f"train_days={train_days} test_days={test_days}"
    )
    trading, data = make_clients()
    del trading
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    bars = get_historical_bars(data, symbol, timeframe_minutes, start=start, end=end, limit=None)
    if bars.empty:
        raise RuntimeError(
            "Optimizer received no historical bars from Alpaca. Check credentials, symbol, market-data access, and the selected lookback window."
        )

    candidates = iter_candidates()
    window_count = len(walk_forward_splits(bars.index, train_days, test_days))
    if window_count == 0:
        raise RuntimeError(
            f"Optimizer could not create any walk-forward windows from {len(bars)} bars. "
            f"Increase RESEARCH_LOOKBACK_DAYS or reduce RESEARCH_TRAIN_DAYS/RESEARCH_TEST_DAYS."
        )
    _log(f"loaded {len(bars)} bars, evaluating {len(candidates)} candidates across {window_count} walk-forward windows")

    _log("evaluating loaded-profile baseline")
    baseline = evaluate_candidate(
        bars=bars,
        timeframe_minutes=timeframe_minutes,
        sizing_mode=sizing_mode,
        base_qty=base_qty,
        starting_equity=starting_equity,
        train_days=train_days,
        test_days=test_days,
        params={},
    )

    results: list[CandidateResult] = []
    started_at = datetime.now(timezone.utc)
    for idx, params in enumerate(candidates, start=1):
        result = evaluate_candidate(
            bars=bars,
            timeframe_minutes=timeframe_minutes,
            sizing_mode=sizing_mode,
            base_qty=base_qty,
            starting_equity=starting_equity,
            train_days=train_days,
            test_days=test_days,
            params=params,
            evidence=evidence,
        )
        results.append(result)
        if idx == 1 or idx % progress_every == 0 or idx == len(candidates):
            elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
            _log(
                f"progress {idx}/{len(candidates)} "
                f"elapsed_seconds={elapsed:.1f} best_score={max(row.score for row in results):.2f}"
            )

    ranked = sorted(results, key=lambda row: row.score, reverse=True)
    apply_acceptance(ranked, baseline)
    accepted = [result for result in ranked if result.accepted]

    payload = {
        "generated_at": str(datetime.now(timezone.utc).astimezone(ET)),
        "symbol": symbol,
        "timeframe_minutes": timeframe_minutes,
        "sizing_mode": sizing_mode,
        "candidate_count": len(ranked),
        "accepted_candidate_count": len(accepted),
        "window_count": window_count,
        "strategy_evidence_loaded": evidence is not None,
        "baseline": _result_payload(baseline),
        "best_candidate": _result_payload(accepted[0] if accepted else ranked[0]) if ranked else None,
        "top_candidates": [_result_payload(result) for result in ranked[:top_n]],
        "accepted_candidates": [_result_payload(result) for result in accepted[:top_n]],
    }

    stem = os.getenv("OPT_OUTPUT_STEM", "optimize_latest").strip() or "optimize_latest"
    write_report(REPORTS_DIR / f"{stem}.md", REPORTS_DIR / f"{stem}.json", payload)
    _log(f"completed candidate_count={len(ranked)} output_stem={stem}")
    print(f"Wrote {REPORTS_DIR / f'{stem}.md'}")
    print(f"Wrote {REPORTS_DIR / f'{stem}.json'}")


if __name__ == "__main__":
    main()

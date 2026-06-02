from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from zoneinfo import ZoneInfo

from bot.metrics import closed_trade_summary
from bot.paths import APP_ROOT, REPORTS_DIR


ET = ZoneInfo("America/New_York")
VALIDATION_MARKER = "runtime_validation_sample"
ENTRY_SIGNALS = {"BUY", "SELL", "LONG", "SHORT"}
FORWARD_RETURN_STEPS = (3, 6, 12, 24)
PASS_REASONS = {"long_entry_filters_passed", "short_entry_filters_passed", "cooldown_overridden_stronger_signal"}


def _table(conn: sqlite3.Connection, name: str) -> pd.DataFrame:
    try:
        return pd.read_sql_query(f"SELECT * FROM {name};", conn)
    except Exception:
        return pd.DataFrame()


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        if pd.isna(value):
            return None
        if value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_json(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _split_reasons(*values: object) -> list[str]:
    reasons: list[str] = []
    for value in values:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        if isinstance(value, list):
            chunks = value
        else:
            chunks = str(value).replace(",", ";").split(";")
        for chunk in chunks:
            token = str(chunk).strip()
            if token and token != VALIDATION_MARKER:
                reasons.append(token)
    return list(dict.fromkeys(reasons))


def _blocker_reasons(reasons: list[str]) -> list[str]:
    return [reason for reason in reasons if reason not in PASS_REASONS]


def _row_is_validation(row: pd.Series | dict[str, Any]) -> bool:
    note = str(row.get("note", "") or "")
    reasons = str(row.get("reasons", "") or "")
    return VALIDATION_MARKER in note or VALIDATION_MARKER in reasons


def _dt_text(value: object) -> str | None:
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return str(ts.tz_convert(ET))


def _symbol_counts(df: pd.DataFrame) -> dict[str, int]:
    if df.empty or "symbol" not in df.columns:
        return {}
    return {str(key): int(value) for key, value in df["symbol"].fillna("n/a").value_counts().sort_index().items()}


def _status_counts(df: pd.DataFrame) -> dict[str, int]:
    if df.empty or "status" not in df.columns:
        return {}
    return {str(key): int(value) for key, value in df["status"].fillna("n/a").value_counts().sort_index().items()}


def _latest_records(df: pd.DataFrame, columns: list[str], limit: int = 10) -> list[dict[str, Any]]:
    if df.empty:
        return []
    available = [column for column in columns if column in df.columns]
    if not available:
        return []
    sort_column = "ts" if "ts" in df.columns else available[0]
    latest = df.sort_values(sort_column).tail(limit)
    return [{key: _json_ready(value) for key, value in row.items()} for row in latest[available].to_dict(orient="records")]


def _run_reason_tokens(row: pd.Series | dict[str, Any]) -> list[str]:
    metrics = _safe_json(row.get("metrics_json"))
    return _split_reasons(
        row.get("note"),
        row.get("reasons"),
        metrics.get("blocker_reasons"),
        metrics.get("decision_reasons"),
    )


def _run_price(row: pd.Series | dict[str, Any]) -> float | None:
    metrics = _safe_json(row.get("metrics_json"))
    return _safe_float(row.get("price")) or _safe_float(metrics.get("price"))


def _run_sort_ts(row: pd.Series | dict[str, Any]) -> pd.Timestamp | None:
    metrics = _safe_json(row.get("metrics_json"))
    candidates = (
        metrics.get("bar_close_ts"),
        row.get("bar_ts"),
        metrics.get("bar_ts"),
        row.get("ts"),
    )
    for value in candidates:
        ts = pd.to_datetime(value, utc=True, errors="coerce")
        if not pd.isna(ts):
            return ts
    return None


def _is_near_miss(row: pd.Series | dict[str, Any]) -> bool:
    signal = str(row.get("signal", "") or "").upper()
    action = str(row.get("desired_action", "") or "").upper()
    reasons = _run_reason_tokens(row)
    has_blocker = bool(_blocker_reasons(reasons))
    has_entry_intent = signal in ENTRY_SIGNALS or any("entry_filters_passed" in reason for reason in reasons)
    blocked_action = action in {"", "HOLD", "NONE", "NAN"}
    return has_entry_intent and blocked_action and has_blocker and _run_price(row) is not None


def _directional_return(signal: str, entry_price: float, future_price: float) -> float:
    raw = (future_price - entry_price) / entry_price
    return -raw if signal.upper() in {"SELL", "SHORT"} else raw


def _forward_return_labels(real_runs: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if real_runs.empty:
        return [], {"steps": {}, "profitable_blocker_counts": {}}

    rows: list[dict[str, Any]] = []
    enriched = real_runs.copy()
    enriched["_sort_ts"] = [(_run_sort_ts(row) or pd.NaT) for _, row in enriched.iterrows()]
    enriched["_price"] = [_run_price(row) for _, row in enriched.iterrows()]
    enriched = enriched.dropna(subset=["_sort_ts", "_price"])
    if enriched.empty:
        return [], {"steps": {}, "profitable_blocker_counts": {}}

    for _, group in enriched.sort_values(["symbol", "_sort_ts"] if "symbol" in enriched.columns else ["_sort_ts"]).groupby(
        "symbol" if "symbol" in enriched.columns else lambda _: "all"
    ):
        group = group.reset_index(drop=True)
        for idx, row in group.iterrows():
            if not _is_near_miss(row):
                continue
            entry_price = _safe_float(row.get("_price"))
            if entry_price is None or entry_price <= 0:
                continue
            signal = str(row.get("signal", "") or "")
            item = {
                "ts": _dt_text(row.get("ts")),
                "symbol": row.get("symbol"),
                "signal": signal,
                "price": entry_price,
                "blockers": _blocker_reasons(_run_reason_tokens(row)),
                "forward_returns": {},
            }
            for step in FORWARD_RETURN_STEPS:
                future_idx = idx + step
                future_return = None
                if future_idx < len(group):
                    future_price = _safe_float(group.loc[future_idx, "_price"])
                    if future_price is not None and future_price > 0:
                        future_return = _directional_return(signal, entry_price, future_price)
                item["forward_returns"][f"+{step}"] = future_return
            rows.append(item)

    step_summary: dict[str, dict[str, Any]] = {}
    profitable_blockers: Counter[str] = Counter()
    for step in FORWARD_RETURN_STEPS:
        key = f"+{step}"
        values = [_safe_float(row["forward_returns"].get(key)) for row in rows]
        values = [value for value in values if value is not None]
        step_summary[key] = {
            "count": len(values),
            "avg_return": (sum(values) / len(values)) if values else None,
            "positive_ratio": (sum(1 for value in values if value > 0) / len(values)) if values else None,
        }
        if step == 12:
            for row in rows:
                value = _safe_float(row["forward_returns"].get(key))
                if value is not None and value > 0:
                    profitable_blockers.update(row["blockers"])

    return rows, {
        "steps": step_summary,
        "profitable_blocker_counts": dict(profitable_blockers.most_common(10)),
    }


def analyze_database(db_path: Path, label: str | None = None) -> dict[str, Any]:
    label = label or str(db_path)
    if not db_path.exists():
        return {
            "label": label,
            "path": str(db_path),
            "exists": False,
            "run_count": 0,
            "validation_run_count": 0,
            "real_run_count": 0,
            "validation_only": False,
            "symbol_counts": {},
            "rejection_counts": {},
            "blocked_entry_count": 0,
            "stale_data_count": 0,
            "order_count": 0,
            "filled_order_count": 0,
            "order_status_counts": {},
            "closed_trade_summary": closed_trade_summary(pd.DataFrame()),
            "near_miss_forward_returns": [],
            "near_miss_summary": {"steps": {}, "profitable_blocker_counts": {}},
            "latest_orders": [],
        }

    conn = sqlite3.connect(db_path)
    try:
        runs = _table(conn, "runs")
        orders = _table(conn, "orders")
        closed = _table(conn, "closed_trades")
    finally:
        conn.close()

    if not runs.empty and "ts" in runs.columns:
        runs["ts"] = pd.to_datetime(runs["ts"], utc=True, errors="coerce")
        runs = runs.dropna(subset=["ts"]).sort_values("ts")

    validation_mask = runs.apply(_row_is_validation, axis=1) if not runs.empty else pd.Series(dtype=bool)
    validation_runs = runs[validation_mask] if not runs.empty else runs
    real_runs = runs[~validation_mask] if not runs.empty else runs

    rejection_counts: Counter[str] = Counter()
    blocked_entry_count = 0
    stale_data_count = 0
    for _, row in real_runs.iterrows():
        blockers = _blocker_reasons(_run_reason_tokens(row))
        rejection_counts.update(blockers)
        if blockers and _is_near_miss(row):
            blocked_entry_count += 1
        if "stale_bar_data" in blockers:
            stale_data_count += 1

    near_misses, near_miss_summary = _forward_return_labels(real_runs)

    if not orders.empty and "ts" in orders.columns:
        orders["ts"] = pd.to_datetime(orders["ts"], utc=True, errors="coerce")
        orders = orders.dropna(subset=["ts"]).sort_values("ts")
    filled_order_count = 0
    if not orders.empty and "status" in orders.columns:
        filled_order_count = int(orders["status"].fillna("").astype(str).str.upper().eq("FILLED").sum())

    closed_summary = closed_trade_summary(closed)

    latest_real_ts = None
    latest_run_ts = None
    if not real_runs.empty and "ts" in real_runs.columns:
        latest_real_ts = _dt_text(real_runs["ts"].max())
    if not runs.empty and "ts" in runs.columns:
        latest_run_ts = _dt_text(runs["ts"].max())

    return {
        "label": label,
        "path": str(db_path),
        "exists": True,
        "run_count": int(len(runs)),
        "validation_run_count": int(len(validation_runs)),
        "real_run_count": int(len(real_runs)),
        "validation_only": bool(len(runs) > 0 and len(real_runs) == 0),
        "latest_run_ts": latest_run_ts,
        "latest_real_run_ts": latest_real_ts,
        "symbol_counts": _symbol_counts(real_runs),
        "validation_symbol_counts": _symbol_counts(validation_runs),
        "rejection_counts": dict(rejection_counts.most_common()),
        "blocked_entry_count": int(blocked_entry_count),
        "stale_data_count": int(stale_data_count),
        "order_count": int(len(orders)),
        "filled_order_count": filled_order_count,
        "order_status_counts": _status_counts(orders),
        "closed_trade_summary": closed_summary,
        "near_miss_forward_returns": near_misses[:50],
        "near_miss_summary": near_miss_summary,
        "latest_orders": _latest_records(
            orders,
            ["ts", "symbol", "side", "qty", "status", "filled_avg_price", "filled_qty", "intent", "action_type", "notes"],
        ),
    }


def discover_database_paths(app_root: Path = APP_ROOT) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    root_db = app_root / "data" / "bot.db"
    if root_db.exists():
        paths.append(("root", root_db))

    runtime_root = app_root / "runtime"
    if runtime_root.exists():
        for db_path in sorted(runtime_root.glob("*/data/bot.db")):
            profile = db_path.parents[1].name
            paths.append((f"runtime/{profile}", db_path))

    seen: set[Path] = set()
    unique: list[tuple[str, Path]] = []
    for label, path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append((label, path))
    return unique


def load_research_evidence(app_root: Path = APP_ROOT) -> dict[str, Any]:
    path = app_root / "reports" / "research_latest.json"
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"exists": False, "path": str(path), "error": "invalid_json"}

    return {
        "exists": True,
        "path": str(path),
        "symbol": payload.get("symbol"),
        "timeframe_minutes": payload.get("timeframe_minutes"),
        "sizing_mode": payload.get("sizing_mode"),
        "full_summary": payload.get("full_summary") or {},
        "by_hour": payload.get("by_hour") or [],
        "by_side": payload.get("by_side") or [],
        "by_session": payload.get("by_session") or [],
        "best_conditions": payload.get("best_conditions") or [],
        "worst_conditions": payload.get("worst_conditions") or [],
        "acceptance": payload.get("acceptance"),
    }


def _research_has_condition(research: dict[str, Any], text: str, bucket: str = "worst_conditions") -> bool:
    return any(text in str(item) for item in research.get(bucket, []))


def recommend_experiments(databases: list[dict[str, Any]], research: dict[str, Any]) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []

    btc_validation_only = [
        db["label"]
        for db in databases
        if db.get("validation_only") and "BTC/USD" in db.get("validation_symbol_counts", {})
    ]
    if btc_validation_only:
        recommendations.append(
            {
                "title": "Fix BTC runtime evidence first",
                "priority": "high",
                "evidence": f"BTC runtime databases are validation-only: {', '.join(btc_validation_only)}.",
                "action": "Verify cron/deployment and collect real BTC paper cycles before promoting strategy changes live.",
            }
        )

    stale_total = sum(int(db.get("stale_data_count") or 0) for db in databases)
    if stale_total > 0:
        recommendations.append(
            {
                "title": "Resolve stale-bar blocking before loosening entries",
                "priority": "high",
                "evidence": f"Historical runs recorded {stale_total} stale-bar blocked entry signal(s).",
                "action": "Tune startup delay/bar-age handling or deployment cadence so good signals are not discarded as stale.",
            }
        )

    if research.get("exists"):
        if _research_has_condition(research, "volume_ratio_bucket=0.8-1.0"):
            recommendations.append(
                {
                    "title": "Keep low-liquidity filters in candidate scoring",
                    "priority": "medium",
                    "evidence": "SPY research marks volume ratio 0.8-1.0 as a weak bucket.",
                    "action": "Reward BTC candidates that require clear participation rather than copying SPY clock windows.",
                }
            )
        if _research_has_condition(research, "hold_bucket=<30m"):
            recommendations.append(
                {
                    "title": "Avoid short failed holds",
                    "priority": "medium",
                    "evidence": "SPY research marks sub-30-minute holds as a weak bucket while 60m+ holds were stronger.",
                    "action": "Prefer BTC candidates that allow winners enough room and avoid churny quick-exit regimes.",
                }
            )
        if _research_has_condition(research, "session_bucket=afternoon"):
            recommendations.append(
                {
                    "title": "Do not transfer SPY afternoon weakness literally to BTC",
                    "priority": "medium",
                    "evidence": "SPY afternoon was weak, but BTC trades 24/7 with different session structure.",
                    "action": "Reuse the pattern class, not the clock: identify BTC-specific liquidity/volatility windows with fresh logs.",
                }
            )

    profitable_blockers: Counter[str] = Counter()
    for db in databases:
        profitable_blockers.update(db.get("near_miss_summary", {}).get("profitable_blocker_counts", {}))
    if profitable_blockers:
        reason, count = profitable_blockers.most_common(1)[0]
        recommendations.append(
            {
                "title": "Review filters that blocked profitable near misses",
                "priority": "medium",
                "evidence": f"Forward labels show {count} profitable +12 observation near-miss(es) blocked by {reason}.",
                "action": "Include this blocker in the next optimizer grid instead of manually relaxing live risk.",
            }
        )

    if not recommendations:
        recommendations.append(
            {
                "title": "Collect more real BTC evidence",
                "priority": "medium",
                "evidence": "No actionable live/paper BTC trading history was found locally.",
                "action": "Run paper BTC long enough to populate signal metrics, near misses, orders, and closed trades.",
            }
        )

    return recommendations


def build_strategy_evidence(app_root: Path = APP_ROOT) -> dict[str, Any]:
    databases = [analyze_database(path, label) for label, path in discover_database_paths(app_root)]
    research = load_research_evidence(app_root)
    return {
        "generated_at": str(datetime.now(timezone.utc).astimezone(ET)),
        "databases": databases,
        "research": research,
        "recommended_experiments": recommend_experiments(databases, research),
    }


def _fmt_num(value: object, digits: int = 4) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:.{digits}f}"


def _fmt_pct(value: object) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Strategy Evidence Report",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Database Coverage",
    ]
    for db in payload.get("databases", []):
        lines.extend(
            [
                f"- {db['label']}: runs={db['run_count']} real={db['real_run_count']} validation={db['validation_run_count']} "
                f"orders={db['order_count']} filled={db['filled_order_count']} closed_trades={db['closed_trade_summary'].get('trade_count', 0)}",
            ]
        )
        if db.get("validation_only"):
            lines.append(f"  - Warning: validation-only database; no real trade cycles found.")
        if db.get("latest_real_run_ts"):
            lines.append(f"  - Latest real run: {db['latest_real_run_ts']}")

    research = payload.get("research", {})
    lines.extend(["", "## Replay Evidence"])
    if research.get("exists"):
        full = research.get("full_summary", {})
        lines.extend(
            [
                f"- Symbol: {research.get('symbol')}",
                f"- Trades: {full.get('trade_count', 0)}",
                f"- Net P&L: {_fmt_num(full.get('net_pnl'))}",
                f"- Profit factor: {_fmt_num(full.get('profit_factor'), 3)}",
                f"- Expectancy: {_fmt_num(full.get('expectancy'))}",
                f"- Trades/day: {_fmt_num(full.get('trades_per_day'), 2)}",
                "",
                "Best conditions:",
                *(research.get("best_conditions") or ["- No repeatable winners yet."]),
                "",
                "Worst conditions:",
                *(research.get("worst_conditions") or ["- No repeatable losers yet."]),
            ]
        )
    else:
        lines.append("- No research report found.")

    lines.extend(["", "## Runtime Diagnostics"])
    for db in payload.get("databases", []):
        lines.append(f"- {db['label']}: blocked_entries={db['blocked_entry_count']} stale_data={db['stale_data_count']}")
        top_rejections = list((db.get("rejection_counts") or {}).items())[:5]
        if top_rejections:
            lines.append("  - Top blockers: " + ", ".join(f"{key}={value}" for key, value in top_rejections))
        near_summary = db.get("near_miss_summary", {}).get("steps", {})
        if near_summary:
            labels = []
            for step, summary in near_summary.items():
                if summary.get("count"):
                    labels.append(f"{step}: avg={_fmt_pct(summary.get('avg_return'))} positive={_fmt_pct(summary.get('positive_ratio'))}")
            if labels:
                lines.append("  - Near-miss forward labels: " + "; ".join(labels))

    lines.extend(["", "## Recommended Experiments"])
    for row in payload.get("recommended_experiments", []):
        lines.append(f"- [{row['priority']}] {row['title']}: {row['action']} Evidence: {row['evidence']}")

    lines.append("")
    return "\n".join(lines)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return str(value)
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def write_reports(payload: dict[str, Any], reports_dir: Path = REPORTS_DIR) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / "strategy_evidence_latest.md"
    json_path = reports_dir / "strategy_evidence_latest.json"
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    json_path.write_text(json.dumps(_json_ready(payload), indent=2, default=str), encoding="utf-8")
    return md_path, json_path


def main() -> None:
    payload = build_strategy_evidence(APP_ROOT)
    md_path, json_path = write_reports(payload, REPORTS_DIR)
    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()

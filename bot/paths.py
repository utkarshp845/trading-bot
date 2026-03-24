from __future__ import annotations

import os
from pathlib import Path


def _resolve_app_root() -> Path:
    configured = os.getenv("BOT_APP_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    docker_root = Path("/app")
    if docker_root.exists():
        return docker_root

    return Path(__file__).resolve().parents[1]


APP_ROOT = _resolve_app_root()
DATA_DIR = Path(os.getenv("BOT_DATA_DIR", APP_ROOT / "data")).expanduser()
LOGS_DIR = Path(os.getenv("BOT_LOGS_DIR", APP_ROOT / "logs")).expanduser()
REPORTS_DIR = Path(os.getenv("BOT_REPORTS_DIR", APP_ROOT / "reports")).expanduser()


def ensure_runtime_dirs() -> None:
    for path in (DATA_DIR, LOGS_DIR, REPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)

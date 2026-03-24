import logging

from bot.paths import LOGS_DIR, ensure_runtime_dirs

def setup_logger() -> logging.Logger:
    logger = logging.getLogger("trading-bot")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger  # avoid duplicate handlers

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    # stdout
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # file
    ensure_runtime_dirs()
    log_path = LOGS_DIR / "bot.log"
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger

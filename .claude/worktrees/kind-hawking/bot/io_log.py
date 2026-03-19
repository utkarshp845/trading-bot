import logging
import os

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
    log_path = os.path.join("/app/logs", "bot.log")
    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
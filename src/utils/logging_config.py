"""Logging compartido: cada corrida del pipeline escribe a consola y a
un archivo logs/pipeline_YYYY-MM-DD.log, para poder auditar corridas
pasadas sin tener que reproducirlas."""

import logging
from datetime import date

from .paths import LOGS_DIR


def get_logger(name: str) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    log_path = LOGS_DIR / f"pipeline_{date.today().isoformat()}.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

"""Unified logging configuration for the API service."""

import logging
import os
import sys


def setup_logger(name: str = "ambmh") -> logging.Logger:
    """Create and configure a logger with console output.

    In production, Docker captures stdout/stderr as container logs,
    so console logging is the preferred approach.
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, log_level, logging.INFO))
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


# Pre-configured loggers
log = setup_logger("ambmh")
scanner_log = setup_logger("ambmh.scanner")
backtest_log = setup_logger("ambmh.backtest")

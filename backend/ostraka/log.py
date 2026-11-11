"""Project logging. Use `get_logger(__name__)`, never print."""

from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure(level: str) -> None:
    logging.basicConfig(level=level.upper(), format=_FORMAT)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

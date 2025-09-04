"""Compatibility logging helper wrapping production logging config."""
from .logging_config import get_logger, setup_logging  # re-export

__all__ = ["get_logger", "setup_logging"]

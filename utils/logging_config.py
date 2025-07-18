"""
Production-ready logging configuration with structured logging and monitoring
"""
import logging
import logging.handlers
import sys
import os
import json
from datetime import datetime
from typing import Any, Dict, Optional
from pathlib import Path


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields if present
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
            
        return json.dumps(log_entry, ensure_ascii=False)


class ProductionLogger:
    """Production-ready logger setup"""
    
    def __init__(self, name: str = "mcp_platform"):
        self.name = name
        self.logger = logging.getLogger(name)
        self._setup_logger()
    
    def _setup_logger(self):
        """Configure logger for production"""
        # Clear existing handlers
        self.logger.handlers.clear()
        
        # Set log level
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.logger.setLevel(getattr(logging, log_level))
        
        # Prevent propagation to root logger
        self.logger.propagate = False
        
        # Create logs directory
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        # Console handler with colored output for development
        if os.getenv("DEBUG", "false").lower() == "true":
            self._add_console_handler()
        
        # File handler for all logs
        self._add_file_handler(log_dir)
        
        # Error file handler for errors only
        self._add_error_handler(log_dir)
        
        # Structured JSON handler for monitoring
        if os.getenv("ENABLE_STRUCTURED_LOGGING", "true").lower() == "true":
            self._add_structured_handler(log_dir)
    
    def _add_console_handler(self):
        """Add colored console handler for development"""
        handler = logging.StreamHandler(sys.stdout)
        
        # Colored formatter for development
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)
        self.logger.addHandler(handler)
    
    def _add_file_handler(self, log_dir: Path):
        """Add rotating file handler"""
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "mcp_platform.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8"
        )
        
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
        )
        
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        self.logger.addHandler(handler)
    
    def _add_error_handler(self, log_dir: Path):
        """Add error-only file handler"""
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "errors.log",
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8"
        )
        
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s\n"
            "%(pathname)s:%(lineno)d in %(funcName)s\n"
        )
        
        handler.setFormatter(formatter)
        handler.setLevel(logging.ERROR)
        self.logger.addHandler(handler)
    
    def _add_structured_handler(self, log_dir: Path):
        """Add structured JSON handler for monitoring"""
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "structured.jsonl",
            maxBytes=20 * 1024 * 1024,  # 20MB
            backupCount=10,
            encoding="utf-8"
        )
        
        handler.setFormatter(StructuredFormatter())
        handler.setLevel(logging.INFO)
        self.logger.addHandler(handler)
    
    def get_logger(self) -> logging.Logger:
        """Get configured logger"""
        return self.logger


class ContextualLogger:
    """Logger with contextual information"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.context: Dict[str, Any] = {}
    
    def set_context(self, **kwargs):
        """Set contextual information"""
        self.context.update(kwargs)
    
    def clear_context(self):
        """Clear contextual information"""
        self.context.clear()
    
    def _log_with_context(self, level: int, msg: str, *args, **kwargs):
        """Log with context"""
        extra = kwargs.pop("extra", {})
        extra["extra_fields"] = {**self.context, **extra.get("extra_fields", {})}
        kwargs["extra"] = extra
        self.logger.log(level, msg, *args, **kwargs)
    
    def debug(self, msg: str, *args, **kwargs):
        self._log_with_context(logging.DEBUG, msg, *args, **kwargs)
    
    def info(self, msg: str, *args, **kwargs):
        self._log_with_context(logging.INFO, msg, *args, **kwargs)
    
    def warning(self, msg: str, *args, **kwargs):
        self._log_with_context(logging.WARNING, msg, *args, **kwargs)
    
    def error(self, msg: str, *args, **kwargs):
        self._log_with_context(logging.ERROR, msg, *args, **kwargs)
    
    def critical(self, msg: str, *args, **kwargs):
        self._log_with_context(logging.CRITICAL, msg, *args, **kwargs)
    
    def exception(self, msg: str, *args, **kwargs):
        kwargs["exc_info"] = True
        self.error(msg, *args, **kwargs)


# Global logger instance
_production_logger = None

def get_logger(name: Optional[str] = None) -> ContextualLogger:
    """Get a production-ready logger instance"""
    global _production_logger
    
    if _production_logger is None:
        _production_logger = ProductionLogger()
    
    logger_name = f"{_production_logger.name}.{name}" if name else _production_logger.name
    base_logger = logging.getLogger(logger_name)
    
    # Inherit configuration from main logger
    if not base_logger.handlers:
        base_logger.parent = _production_logger.get_logger()
    
    return ContextualLogger(base_logger)


def setup_logging():
    """Setup logging for the entire application"""
    global _production_logger
    _production_logger = ProductionLogger()
    
    # Configure root logger to prevent other libraries from interfering
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    
    # Remove default handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    return _production_logger.get_logger()

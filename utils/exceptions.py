"""
Production-ready exception handling with retry logic and circuit breakers
"""
import asyncio
import functools
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Type, Union
from enum import Enum
import logging

from utils.logging_config import get_logger

logger = get_logger("exceptions")


class ErrorCode(str, Enum):
    """Standardized error codes"""
    # Network errors
    CONNECTION_FAILED = "CONNECTION_FAILED"
    TIMEOUT = "TIMEOUT" 
    RATE_LIMITED = "RATE_LIMITED"
    
    # Authentication/Authorization
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    
    # Data errors
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    DUPLICATE_ERROR = "DUPLICATE_ERROR"
    
    # System errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    
    # MCP specific
    MCP_SERVER_ERROR = "MCP_SERVER_ERROR"
    MCP_TOOL_ERROR = "MCP_TOOL_ERROR"
    LLM_ERROR = "LLM_ERROR"


class MCPError(Exception):
    """Base exception for MCP platform"""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.cause = cause
        self.timestamp = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
            "cause": str(self.cause) if self.cause else None
        }


class RetryConfig:
    """Configuration for retry logic"""
    
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


class CircuitBreaker:
    """Circuit breaker pattern implementation"""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: float = 60.0,
        expected_exception: Type[Exception] = Exception
    ):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.expected_exception = expected_exception
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    
    def call(self, func: Callable, *args, **kwargs):
        """Execute function with circuit breaker"""
        if self.state == "OPEN":
            if time.time() - self.last_failure_time < self.timeout:
                raise MCPError(
                    "Circuit breaker is OPEN",
                    ErrorCode.SERVICE_UNAVAILABLE,
                    {"circuit_breaker": "OPEN", "timeout": self.timeout}
                )
            else:
                self.state = "HALF_OPEN"
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise
    
    def _on_success(self):
        """Handle successful execution"""
        self.failure_count = 0
        self.state = "CLOSED"
    
    def _on_failure(self):
        """Handle failed execution"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            logger.warning(
                f"Circuit breaker opened after {self.failure_count} failures",
                extra={"extra_fields": {"circuit_breaker_state": "OPEN"}}
            )


def with_retry(
    retry_config: Optional[RetryConfig] = None,
    exceptions: tuple = (Exception,)
):
    """Decorator for adding retry logic to functions"""
    if retry_config is None:
        retry_config = RetryConfig()
    
    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(retry_config.max_attempts):
                try:
                    if asyncio.iscoroutinefunction(func):
                        return await func(*args, **kwargs)
                    else:
                        return func(*args, **kwargs)
                        
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == retry_config.max_attempts - 1:
                        break
                    
                    # Calculate delay with exponential backoff
                    delay = min(
                        retry_config.base_delay * (retry_config.exponential_base ** attempt),
                        retry_config.max_delay
                    )
                    
                    # Add jitter
                    if retry_config.jitter:
                        import random
                        delay *= (0.5 + random.random() * 0.5)
                    
                    logger.warning(
                        f"Attempt {attempt + 1} failed, retrying in {delay:.2f}s",
                        extra={
                            "extra_fields": {
                                "attempt": attempt + 1,
                                "max_attempts": retry_config.max_attempts,
                                "delay": delay,
                                "error": str(e)
                            }
                        }
                    )
                    
                    await asyncio.sleep(delay)
            
            # All retries exhausted
            raise MCPError(
                f"Operation failed after {retry_config.max_attempts} attempts",
                ErrorCode.INTERNAL_ERROR,
                {"last_error": str(last_exception)},
                cause=last_exception
            )
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            # For sync functions, convert to async temporarily
            async def async_func():
                return func(*args, **kwargs)
            
            try:
                loop = asyncio.get_event_loop()
                return loop.run_until_complete(async_wrapper(*args, **kwargs))
            except RuntimeError:
                # No event loop, run with new one
                return asyncio.run(async_wrapper(*args, **kwargs))
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def safe_execute(
    func: Callable,
    default_return: Any = None,
    log_errors: bool = True,
    reraise: bool = False
) -> Any:
    """Safely execute function with error handling"""
    try:
        if asyncio.iscoroutinefunction(func):
            # For async functions, return coroutine
            return func()
        else:
            return func()
    except Exception as e:
        if log_errors:
            logger.exception(f"Error in {func.__name__}: {e}")
        
        if reraise:
            raise
        
        return default_return


async def safe_execute_async(
    func: Callable,
    default_return: Any = None,
    log_errors: bool = True,
    reraise: bool = False
) -> Any:
    """Safely execute async function with error handling"""
    try:
        if asyncio.iscoroutinefunction(func):
            return await func()
        else:
            return func()
    except Exception as e:
        if log_errors:
            logger.exception(f"Error in {func.__name__}: {e}")
        
        if reraise:
            raise
        
        return default_return


class ErrorCollector:
    """Collect and aggregate errors for batch processing"""
    
    def __init__(self, max_errors: int = 100):
        self.errors: List[MCPError] = []
        self.max_errors = max_errors
    
    def add_error(self, error: MCPError):
        """Add error to collection"""
        self.errors.append(error)
        
        if len(self.errors) > self.max_errors:
            self.errors.pop(0)  # Remove oldest error
    
    def get_errors(self) -> List[MCPError]:
        """Get all collected errors"""
        return self.errors.copy()
    
    def get_error_summary(self) -> Dict[str, Any]:
        """Get summary of errors"""
        if not self.errors:
            return {"total": 0, "by_code": {}}
        
        summary = {"total": len(self.errors), "by_code": {}}
        
        for error in self.errors:
            code = error.error_code
            if code not in summary["by_code"]:
                summary["by_code"][code] = 0
            summary["by_code"][code] += 1
        
        return summary
    
    def clear(self):
        """Clear all errors"""
        self.errors.clear()


def create_error_response(
    error: Union[Exception, MCPError],
    include_traceback: bool = False
) -> Dict[str, Any]:
    """Create standardized error response"""
    if isinstance(error, MCPError):
        response = {
            "success": False,
            "error": error.to_dict()
        }
    else:
        response = {
            "success": False,
            "error": {
                "error_code": ErrorCode.INTERNAL_ERROR,
                "message": str(error),
                "details": {},
                "timestamp": time.time()
            }
        }
    
    if include_traceback:
        response["error"]["traceback"] = traceback.format_exc()
    
    return response


# Global error collector for monitoring
error_collector = ErrorCollector()

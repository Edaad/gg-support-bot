"""ToolError helpers and translation of repo exceptions into model-readable errors."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.exc import OperationalError


def bad_input(message: str) -> ToolError:
    return ToolError(f"invalid input: {message}")


def not_found(message: str) -> ToolError:
    return ToolError(f"not found: {message}")


def translate_exception(exc: BaseException) -> ToolError | None:
    """Map known repo exceptions to a ToolError; None means re-raise as a crash."""
    if isinstance(exc, ToolError):
        return exc
    try:
        from fastapi import HTTPException
    except ImportError:  # pragma: no cover - fastapi is a hard dependency
        HTTPException = None  # type: ignore[assignment]
    if HTTPException is not None and isinstance(exc, HTTPException):
        return ToolError(f"{exc.status_code}: {exc.detail}")
    if isinstance(exc, OperationalError) and "statement timeout" in str(exc).lower():
        return ToolError(
            "query exceeded the statement timeout; narrow the filters "
            "(club, date range, q) or lower the limit"
        )
    if isinstance(exc, LookupError):
        return not_found(str(exc) or exc.__class__.__name__)
    if isinstance(exc, ValueError):
        return bad_input(str(exc) or exc.__class__.__name__)
    return None


def translate_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: convert HTTPException/ValueError/LookupError/timeouts to ToolError.

    ``functools.wraps`` keeps the real signature visible to the SDK's schema builder.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                translated = translate_exception(exc)
                if translated is None:
                    raise
                raise translated from exc

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            translated = translate_exception(exc)
            if translated is None:
                raise
            raise translated from exc

    return wrapper

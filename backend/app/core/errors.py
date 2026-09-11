from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

log = get_logger("errors")


class StudioError(Exception):
    """Base application error.

    `user_message` + `suggested_action` are safe to show in the UI.
    `detail` is technical and is only written to logs (§37: never show raw
    stack traces to end users).
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "studio_error"

    def __init__(
        self,
        user_message: str,
        *,
        detail: Optional[str] = None,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        suggested_action: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> None:
        self.user_message = user_message
        self.detail = detail or user_message
        self.suggested_action = suggested_action
        self.meta = meta or {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        super().__init__(self.detail)


class NotFoundError(StudioError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class AuthError(StudioError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(StudioError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class ConflictError(StudioError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class ValidationFailed(StudioError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_failed"


class ModelNotInstalledError(StudioError):
    """§42.3 — clearly state when a model is not installed."""

    status_code = status.HTTP_409_CONFLICT
    code = "model_not_installed"

    def __init__(self, model: str, install_hint: Optional[str] = None) -> None:
        super().__init__(
            user_message=f"Model not installed: {model}.",
            detail=f"Adapter '{model}' was requested but its weights/runtime are not installed.",
            suggested_action=install_hint
            or "Open Model Manager and install this model, or select a different model.",
            meta={"model": model},
        )


class ProviderNotConfiguredError(StudioError):
    status_code = status.HTTP_409_CONFLICT
    code = "provider_not_configured"

    def __init__(self, provider: str, env_var: Optional[str] = None) -> None:
        super().__init__(
            user_message=f"Provider not configured: {provider}.",
            detail=f"Provider '{provider}' has no credentials configured.",
            suggested_action=(
                f"Set {env_var} in your .env file."
                if env_var
                else "Configure this provider in Settings → AI Providers."
            ),
            meta={"provider": provider, "env_var": env_var},
        )


class SafetyBlockedError(StudioError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "safety_blocked"


class JobCancelled(Exception):
    """Raised inside a worker when a running job is cancelled."""


def _friendly(exc: StudioError) -> JSONResponse:
    if exc.status_code >= 500:
        log.error("unhandled_application_error", code=exc.code, detail=exc.detail, meta=exc.meta)
    else:
        log.warning("application_error", code=exc.code, detail=exc.detail, meta=exc.meta)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.user_message,
                "suggested_action": exc.suggested_action,
                "meta": exc.meta,
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StudioError)
    async def _studio_error(request: Request, exc: StudioError) -> JSONResponse:
        return _friendly(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        fields = [{"field": ".".join(str(p) for p in e.get("loc", ())[1:]), "message": e.get("msg", "")} for e in errors]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "validation_failed",
                    "message": "Some fields need attention.",
                    "suggested_action": "Review the highlighted fields and try again.",
                    "meta": {"fields": fields},
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak stack traces to users (§37); log everything for developers.
        log.exception("unhandled_exception", path=str(request.url.path), error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Something went wrong on our side.",
                    "suggested_action": "Retry. If it keeps happening, check the server logs under backend/data/logs.",
                    "meta": {},
                }
            },
        )

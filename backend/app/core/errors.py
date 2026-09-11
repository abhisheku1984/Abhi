"""Typed error codes.

The frontend maps these to explicit, honest UI states:
  MODEL_NOT_AVAILABLE              -> "MODEL NOT AVAILABLE"
  HARDWARE_REQUIREMENT_NOT_MET     -> "HARDWARE REQUIREMENT NOT MET"
  EXTERNAL_PROVIDER_REQUIRED       -> "EXTERNAL PROVIDER REQUIRED"
  DEPENDENCY_MISSING               -> shows the exact pip/ffmpeg install command
Never fabricate a result to avoid raising one of these.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class ErrorCode:
    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    HARDWARE_REQUIREMENT_NOT_MET = "HARDWARE_REQUIREMENT_NOT_MET"
    EXTERNAL_PROVIDER_REQUIRED = "EXTERNAL_PROVIDER_REQUIRED"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    UNAUTHORIZED = "UNAUTHORIZED"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class StudioError(Exception):
    """Domain error carrying a stable machine-readable code."""

    def __init__(
        self,
        message: str,
        code: str = ErrorCode.INTERNAL_ERROR,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
        remediation: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        self.remediation = remediation

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details,
            "remediation": self.remediation,
        }


def http_error(
    message: str,
    code: str = ErrorCode.INVALID_REQUEST,
    status_code: int = status.HTTP_400_BAD_REQUEST,
    details: dict[str, Any] | None = None,
    remediation: str = "",
) -> HTTPException:
    body = {"error": code, "message": message, "details": details or {}, "remediation": remediation}
    return HTTPException(status_code=status_code, detail=body)

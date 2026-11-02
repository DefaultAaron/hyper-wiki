"""Structured errors with stable codes shared by every interface."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
    INVALID_DOCUMENT = "invalid_document"
    INVALID_PATH = "invalid_path"
    NOT_FOUND = "not_found"
    UNSUPPORTED_DATABASE_VERSION = "unsupported_database_version"
    SPENDING_CAP_REQUIRED = "spending_cap_required"
    SPENDING_CAP_REACHED = "spending_cap_reached"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_ERROR = "provider_error"


class ErrorInfo(BaseModel):
    """JSON-ready description of an error, as returned by the CLI and MCP tools."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode
    message: str
    details: dict[str, Any] = {}


class HyperWikiError(Exception):
    """An expected failure with a stable code and JSON-serializable details."""

    def __init__(
        self, code: ErrorCode, message: str, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_info(self) -> ErrorInfo:
        return ErrorInfo(code=self.code, message=self.message, details=self.details)

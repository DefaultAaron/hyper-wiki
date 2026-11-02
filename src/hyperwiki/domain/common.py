"""Base contract types, shared field types, and versioned document parsing."""

import json
from collections.abc import Mapping
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from hyperwiki.errors import ErrorCode, HyperWikiError


class Contract(BaseModel):
    """Immutable model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class VersionedDocument(Contract):
    """A top-level document that carries an explicit schema version."""

    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[int]] = frozenset({1})

    schema_version: int


ZoteroKey = Annotated[str, StringConstraints(pattern=r"^[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
EntityKind = Literal["collection", "item", "attachment", "note", "annotation"]
ProviderKind = Literal["api", "local", "subscription"]


class Problem(Contract):
    """A non-fatal issue found while reading a library, reported rather than skipped."""

    code: ErrorCode
    entity: EntityKind
    key: str
    message: str


def parse_document[T: VersionedDocument](model: type[T], data: object) -> T:
    """Validate ``data`` as ``model``, raising ``HyperWikiError`` with a stable code."""
    if not isinstance(data, Mapping):
        raise HyperWikiError(
            ErrorCode.INVALID_DOCUMENT,
            f"{model.__name__} must be a JSON object",
            {"type": type(data).__name__},
        )
    version = data.get("schema_version")
    supported = model.SUPPORTED_SCHEMA_VERSIONS
    if isinstance(version, bool) or not isinstance(version, int) or version not in supported:
        raise HyperWikiError(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            f"unsupported {model.__name__} schema version: {version!r}",
            {"schema_version": version, "supported": sorted(supported)},
        )
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise HyperWikiError(
            ErrorCode.INVALID_DOCUMENT,
            f"invalid {model.__name__}",
            {"errors": json.loads(exc.json(include_url=False))},
        ) from exc

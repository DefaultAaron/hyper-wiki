"""Fingerprints that identify a unit of processing work by everything it depends on."""

import hashlib
import json
from typing import Annotated

from pydantic import (
    Field,
    FiniteFloat,
    PositiveInt,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
)

from hyperwiki.domain.common import Contract

NonEmptyStr = Annotated[str, Field(min_length=1)]
OperationName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")]
FingerprintId = Annotated[str, StringConstraints(pattern=r"^fp_[0-9a-f]{32}$")]
ConfigValue = StrictStr | StrictInt | StrictBool | FiniteFloat | None


class Component(Contract):
    """A tool or model that shapes the output, such as a PDF extractor or embedding model."""

    name: NonEmptyStr
    version: NonEmptyStr
    runtime: str | None = None


class WorkFingerprint(Contract):
    """Everything a unit of work depends on; any change means the work must be redone.

    ``inputs`` names the versions read (for example an attachment version and its hash),
    ``schema_version`` is the version of the output records, and prompt versions belong in
    ``config``.
    """

    operation: OperationName
    operation_version: PositiveInt
    inputs: Annotated[dict[NonEmptyStr, NonEmptyStr], Field(min_length=1)]
    extractor: Component | None = None
    model: Component | None = None
    schema_version: PositiveInt
    config: dict[NonEmptyStr, ConfigValue] = Field(default_factory=dict)


def fingerprint_id(fingerprint: WorkFingerprint) -> FingerprintId:
    """Hash the canonical JSON form; key order and formatting never change the ID."""
    canonical = json.dumps(
        fingerprint.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "fp_" + hashlib.sha256(canonical).hexdigest()[:32]

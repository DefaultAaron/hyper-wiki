"""Fingerprints that identify a unit of processing work by everything it depends on."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    Field,
    FiniteFloat,
    NonNegativeInt,
    PositiveInt,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
)

from hyperwiki.domain.common import Contract
from hyperwiki.errors import ErrorInfo

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


JobState = Literal["queued", "leased", "done", "failed", "cancelled"]
EnqueueOutcome = Literal["queued", "already_queued", "already_done"]


class Job(Contract):
    """A queued, running, or finished unit of work.

    A ``leased`` job belongs to ``lease_owner`` until ``lease_expires_at``; after that another
    worker may reclaim it. ``attempts`` counts claims, including ones whose worker died.
    """

    id: str
    operation: OperationName
    subject: str
    fingerprint: FingerprintId
    spec: WorkFingerprint
    state: JobState
    priority: int
    forced: bool
    attempts: NonNegativeInt
    max_attempts: PositiveInt
    lease_owner: str | None
    lease_expires_at: AwareDatetime | None
    not_before: AwareDatetime
    last_error: ErrorInfo | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EnqueueResult(Contract):
    """``job_id`` is the new job, the job already open, or the job that produced the result."""

    outcome: EnqueueOutcome
    job_id: str
    fingerprint: FingerprintId

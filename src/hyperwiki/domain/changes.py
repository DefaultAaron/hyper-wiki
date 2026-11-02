"""Change events observed in a library between snapshots or notifications."""

from typing import Literal

from pydantic import AwareDatetime, NonNegativeInt

from hyperwiki.domain.common import Contract, EntityKind, ZoteroKey

ChangeKind = Literal["upsert", "trash", "restore", "delete"]


class ChangeEvent(Contract):
    entity: EntityKind
    key: ZoteroKey
    change: ChangeKind
    version: NonNegativeInt
    observed_at: AwareDatetime

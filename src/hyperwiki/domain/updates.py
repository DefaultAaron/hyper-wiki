"""Update modes and settings (product design section 6)."""

from datetime import datetime, timedelta
from typing import Literal

from hyperwiki.domain.common import Contract, ZoteroKey

Schedule = Literal["daily", "weekly", "fortnightly", "monthly", "off"]
SCHEDULE_DAYS: dict[Schedule, int] = {"daily": 1, "weekly": 7, "fortnightly": 14, "monthly": 30}

# What started an update: a Zotero change (automatic), the schedule (scheduled), the user's
# "update all now" (all) or "update selected" (selected), or queued work run between syncs
# (queue).
UpdateTrigger = Literal["automatic", "scheduled", "all", "selected", "queue"]


class UpdateSettings(Contract):
    """Workspace-wide update settings.

    ``automatic`` syncs when the library changes; ``schedule`` sets how often a full check runs;
    ``paused`` holds automatic and scheduled processing (syncing continues, and the work waits
    in the pending list).
    """

    automatic: bool = True
    schedule: Schedule = "daily"
    paused: bool = False


class Selection(Contract):
    """Items, and collections with their subcollections, chosen for "update selected"."""

    item_keys: frozenset[ZoteroKey] = frozenset()
    collection_keys: frozenset[ZoteroKey] = frozenset()


def next_full_check(settings: UpdateSettings, last: datetime | None) -> datetime | None:
    """When the next scheduled full check is due; None if the schedule is off or none ran."""
    if settings.schedule == "off" or last is None:
        return None
    return last + timedelta(days=SCHEDULE_DAYS[settings.schedule])


def full_check_due(settings: UpdateSettings, last: datetime | None, now: datetime) -> bool:
    if settings.schedule == "off":
        return False
    due = next_full_check(settings, last)
    return due is None or now >= due

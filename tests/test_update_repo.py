import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hyperwiki.domain.updates import Schedule, UpdateSettings, full_check_due, next_full_check
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.services.evidence import plan_extraction
from hyperwiki.services.library_import import import_snapshot
from hyperwiki.storage.library_repo import stable_id, tombstone
from hyperwiki.storage.update_repo import (
    PendingItem,
    exclude_collection,
    excluded_collections,
    excluded_item_ids,
    get_update_settings,
    include_collection,
    last_full_check,
    pending_items,
    record_full_check,
    set_update_settings,
)
from snapshot_mutations import load_manifest_dict, write_manifest_dict

T = datetime(2026, 11, 4, 9, 30, tzinfo=UTC)


def _file_in_two_collections(snapshot: Path) -> None:
    """IT2DUP22 is in Unread; also file it under Deep Learning."""
    data = load_manifest_dict(snapshot)
    item = next(i for i in data["items"] if i["key"] == "IT2DUP22")
    item["collection_keys"] = ["CL2DEEP2", "CL2UNRD2"]
    write_manifest_dict(snapshot, data)


def _keys(conn: sqlite3.Connection, library_id: str, item_ids: set[str]) -> set[str]:
    by_id = {
        stable_id(library_id, "item", row["key"]): row["key"]
        for row in conn.execute("SELECT key FROM items")
    }
    return {by_id[item_id] for item_id in item_ids}


def test_settings_default_and_round_trip(database: sqlite3.Connection) -> None:
    assert get_update_settings(database) == UpdateSettings(
        automatic=True, schedule="daily", paused=False
    )
    changed = set_update_settings(database, schedule="weekly", paused=True)
    assert changed == UpdateSettings(automatic=True, schedule="weekly", paused=True)
    assert get_update_settings(database) == changed
    assert set_update_settings(database, automatic=False) == UpdateSettings(
        automatic=False, schedule="weekly", paused=True
    )


def test_full_check_time_round_trip(database: sqlite3.Connection) -> None:
    assert last_full_check(database) is None
    record_full_check(database, T)
    assert last_full_check(database) == T


def test_next_full_check_by_schedule() -> None:
    days: dict[Schedule, int] = {"daily": 1, "weekly": 7, "fortnightly": 14, "monthly": 30}
    for schedule, count in days.items():
        settings = UpdateSettings(schedule=schedule)
        assert next_full_check(settings, T) == T + timedelta(days=count)
    assert next_full_check(UpdateSettings(schedule="off"), T) is None
    assert next_full_check(UpdateSettings(), None) is None

    assert full_check_due(UpdateSettings(), None, T)
    assert not full_check_due(UpdateSettings(schedule="off"), None, T)
    assert not full_check_due(UpdateSettings(), T, T + timedelta(hours=23))
    assert full_check_due(UpdateSettings(), T, T + timedelta(days=1))


def test_exclusion_covers_subcollections_keeps_multi_filed(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    _file_in_two_collections(bilingual_snapshot)
    library_id = import_snapshot(database, bilingual_snapshot).library_id
    assert exclude_collection(database, "CL2DEEP2") == [library_id]
    assert excluded_collections(database) == {library_id: ["CL2DEEP2"]}
    excluded = excluded_item_ids(database, library_id)
    # IT2ENG22 and IT2ZHN22 are in the subcollection 注意力机制; IT2DUP22 is also in Unread;
    # IT2NAT22 is unfiled.
    assert _keys(database, library_id, excluded) == {"IT2ENG22", "IT2ZHN22", "IT2MIX22"}
    english = stable_id(library_id, "item", "IT2ENG22")
    unfiled = stable_id(library_id, "item", "IT2NAT22")
    assert excluded_item_ids(database, library_id, [english, unfiled]) == {english}

    assert include_collection(database, "CL2DEEP2") == [library_id]
    assert excluded_collections(database) == {}
    assert excluded_item_ids(database, library_id) == set()


def test_exclude_unknown_collection_is_not_found(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    import_snapshot(database, bilingual_snapshot)
    for change in (exclude_collection, include_collection):
        with pytest.raises(HyperWikiError) as caught:
            change(database, "CLMISS22")
        assert caught.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(HyperWikiError) as caught:
        include_collection(database, "CL2DEEP2")  # known, but not excluded
    assert caught.value.code == ErrorCode.NOT_FOUND


def test_tombstoned_collection_is_not_a_membership(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    _file_in_two_collections(bilingual_snapshot)
    library_id = import_snapshot(database, bilingual_snapshot).library_id
    exclude_collection(database, "CL2DEEP2")
    with database:
        tombstone(database, library_id, "collection", "CL2UNRD2", at=T)
    excluded = _keys(database, library_id, excluded_item_ids(database, library_id))
    assert excluded == {"IT2ENG22", "IT2ZHN22", "IT2MIX22", "IT2DUP22"}
    with pytest.raises(HyperWikiError):
        exclude_collection(database, "CL2UNRD2")


def test_pending_items_group_jobs_by_item(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = import_snapshot(database, bilingual_snapshot).library_id
    assert pending_items(database) == []
    plan_extraction(database, now=lambda: T)
    assert pending_items(database) == [
        PendingItem(
            library_id=library_id,
            item_key="IT2ENG22",
            title="A Synthetic Study of Attention Methods",
            attachment_keys=["AT2ENG22"],
            jobs=1,
        ),
        PendingItem(
            library_id=library_id,
            item_key="IT2ZHN22",
            title="基于注意力机制的合成实验研究",
            attachment_keys=["AT2ZHN22"],
            jobs=1,
        ),
    ]

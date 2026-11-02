from typing import Any

NOW = "2026-11-02T00:00:00Z"


def minimal_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "adapter": "snapshot",
        "library": {"id": "lib-1", "kind": "user", "name": "Test"},
        "snapshot_id": "snap-1",
        "created_at": NOW,
        "completeness": "complete",
        "storage_root": "storage",
        "collections": [
            {"key": "CL2DEEP2", "version": 1, "name": "Deep Learning", "parent_key": None}
        ],
        "items": [
            {
                "key": "IT2ENG22",
                "version": 1,
                "item_type": "journalArticle",
                "title": "T",
                "creators": [{"creator_type": "author", "last_name": "Example"}],
                "date": "2025",
                "doi": None,
                "url": None,
                "abstract": None,
                "language": "en",
                "tags": ["a"],
                "collection_keys": ["CL2DEEP2"],
                "extra": {},
                "trashed": False,
                "date_modified": NOW,
            }
        ],
        "attachments": [
            {
                "key": "AT2ENG22",
                "version": 1,
                "parent_key": "IT2ENG22",
                "content_type": "application/pdf",
                "link_mode": "imported_file",
                "path": "AT2ENG22/paper.pdf",
                "url": None,
                "trashed": False,
                "date_modified": NOW,
            }
        ],
        "notes": [
            {
                "key": "NT2ENG22",
                "version": 1,
                "parent_key": "IT2ENG22",
                "html": "<p>n</p>",
                "trashed": False,
                "date_modified": NOW,
            }
        ],
        "annotations": [
            {
                "key": "AN2ENG22",
                "version": 1,
                "parent_key": "AT2ENG22",
                "annotation_type": "highlight",
                "text": "t",
                "comment": None,
                "page_label": "1",
                "color": "#ffd400",
                "date_modified": NOW,
            }
        ],
    }

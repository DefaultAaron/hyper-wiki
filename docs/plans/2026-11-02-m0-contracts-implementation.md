# M0 Contracts, Fixtures, Storage, and Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the M0 foundations every later milestone depends on: versioned contracts, bilingual synthetic fixtures, SQLite storage, a read-only snapshot importer with a JSON CLI, the AI-provider contract, and CI on three operating systems.

**Architecture:** Pydantic models in `hyperwiki.domain` define every contract and fail with stable error codes. A snapshot adapter reads a Zotero-shaped directory without modifying it; an import service writes it into SQLite in one transaction; a thin argparse CLI emits UTF-8 JSON envelopes. Providers sit behind a `TextGenerator` protocol with deterministic fakes, a spending guard, an in-memory subscription queue, and an OpenAI-compatible HTTP client.

**Tech Stack:** Python 3.12, uv, pydantic 2 (MIT), httpx (BSD-3-Clause), sqlite3 (stdlib), argparse (stdlib), pytest, mypy `--strict` with the pydantic plugin, ruff.

**Spec:** [PRODUCT_DESIGN.md](../../PRODUCT_DESIGN.md); M0 in [DEVELOPMENT_PLAN.md](../../DEVELOPMENT_PLAN.md); HW-002–HW-005, HW-009, HW-010 in [BACKLOG.md](BACKLOG.md); contracts in [ADR 0001](../decisions/0001-core-architecture.md).

**Out of scope:** the HW-006 (Zotero change detection), HW-007 (PDF extraction), and HW-008 (bilingual search) spikes. They need a live Zotero library and licensed PDFs from the user and are investigations, not test-first tasks; run them separately and record ADRs.

**Branch:** work on `m0/contracts` from `main`; merge after Task 11's CI run is green on all three operating systems.

## Global Constraints

- Python 3.12 (`.python-version`), `requires-python = ">=3.12"`; every check runs as `uv run --locked …`.
- All checks must pass after every task: `uv run --locked ruff check`, `uv run --locked ruff format --check`, `uv run --locked mypy`, `uv run --locked pytest`, `uv run --locked python scripts/check_docs.py`.
- mypy is strict and covers `src`, `tests`, and `scripts`; test functions and fixtures are fully annotated.
- New runtime dependencies must satisfy [ADR 0002](../decisions/0002-license.md); this plan adds only `pydantic` and `httpx`.
- Hyper-Wiki only reads Zotero data. Nothing in this plan writes inside a snapshot directory.
- Every text file is read and written with `encoding="utf-8"`; English and Chinese text must round-trip unchanged.
- Tests make no network calls except the opt-in `live` smoke test (Task 10).
- API keys are never stored in configuration or printed; configuration names an environment variable.
- An `api` provider without `daily_spend_cap_usd` is rejected with `spending_cap_required`.
- The CLI writes one JSON document per invocation to stdout as UTF-8 bytes; diagnostics go to stderr.
- Test files live flat in `tests/` with unique names (mypy treats them as top-level modules); shared helpers are plain modules in `tests/`.
- Close every SQLite connection; Windows cannot delete open database files during tmp cleanup.

## Review Focus

1. **Windows line-ending conversion of fixture files** changes their bytes and therefore their SHA-256; hashes must be identical on every OS. Pinned by `.gitattributes` and `test_fixture_checksums_match` (Task 5), exercised on Windows in Task 11.
2. **Attachment paths that escape the storage root** (`..`, absolute paths, drive letters, backslashes, symlinks) and linked files outside allowed roots must be reported as `invalid_path` and never hashed or read. Pinned by `test_path_escape_is_reported_not_read`, `test_symlink_escaping_storage_is_invalid`, and `test_linked_file_roots` (Task 7).
3. **Chinese text** in titles, tags, filenames, notes, and library names must survive manifest parsing, SQLite, and CLI output even on a non-UTF-8 console. Pinned by `test_chinese_text_round_trips` (Task 2), `test_chinese_text_round_trips_through_database` (Task 6), `test_chinese_filename_resolves` (Task 7), and `test_output_is_utf8_json_with_chinese` (Task 8).
4. **Re-importing the same snapshot, or a partial snapshot after a complete one,** must create no duplicates, delete nothing, and keep internal IDs stable. Pinned by `test_reimport_creates_no_duplicates_and_keeps_ids` and `test_partial_after_complete_deletes_nothing` (Task 8).
5. **A failure mid-import, or a database written by a newer Hyper-Wiki,** must leave no half-written state and report a clear error code. Pinned by `test_failed_import_leaves_no_partial_state` (Task 8) and `test_newer_database_is_rejected` (Task 6).

---

### Task 1: Errors and contract base

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (add `pydantic`; enable `plugins = ["pydantic.mypy"]` under `[tool.mypy]`)
- Create: `src/hyperwiki/errors.py`, `src/hyperwiki/domain/__init__.py`, `src/hyperwiki/domain/common.py`
- Test: `tests/test_errors_and_common.py`

**Interfaces:**
- Produces (`hyperwiki.errors`):
  - `class ErrorCode(StrEnum)` with exactly: `UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"`, `INVALID_DOCUMENT = "invalid_document"`, `INVALID_PATH = "invalid_path"`, `NOT_FOUND = "not_found"`, `UNSUPPORTED_DATABASE_VERSION = "unsupported_database_version"`, `SPENDING_CAP_REQUIRED = "spending_cap_required"`, `SPENDING_CAP_REACHED = "spending_cap_reached"`, `PROVIDER_NOT_CONFIGURED = "provider_not_configured"`, `PROVIDER_ERROR = "provider_error"`.
  - `class ErrorInfo(BaseModel)`: `code: ErrorCode`, `message: str`, `details: dict[str, Any] = {}`.
  - `class HyperWikiError(Exception)`: `__init__(self, code: ErrorCode, message: str, details: Mapping[str, Any] | None = None)`; attributes `code`, `message`, `details: dict[str, Any]`; `to_info(self) -> ErrorInfo`; `str(error) == f"{code}: {message}"`.
- Produces (`hyperwiki.domain.common`):
  - `class Contract(BaseModel)` with `model_config = ConfigDict(extra="forbid", frozen=True)`.
  - `class VersionedDocument(Contract)`: `SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[int]] = frozenset({1})`; field `schema_version: int`.
  - `ZoteroKey = Annotated[str, StringConstraints(pattern=r"^[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}$")]`
  - `Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]`
  - `EntityKind = Literal["collection", "item", "attachment", "note", "annotation"]`
  - `ProviderKind = Literal["api", "local", "subscription"]`
  - `class Problem(Contract)`: `code: ErrorCode`, `entity: EntityKind`, `key: str`, `message: str`.
  - `def parse_document[T: VersionedDocument](model: type[T], data: object) -> T`

- [ ] **Step 1: Add the dependency**

Run: `uv add pydantic` (PyPI downloads can be slow on this network; allow several minutes). Add `plugins = ["pydantic.mypy"]` to `[tool.mypy]`.
Expected: `pyproject.toml` lists `pydantic>=2`; `uv.lock` updated; `uv run --locked mypy` still passes.

- [ ] **Step 2: Write the failing tests** in `tests/test_errors_and_common.py`

```python
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from hyperwiki.domain.common import VersionedDocument, ZoteroKey, parse_document
from hyperwiki.errors import ErrorCode, HyperWikiError


class Example(VersionedDocument):
    name: str


def test_parse_document_accepts_supported_version() -> None:
    assert parse_document(Example, {"schema_version": 1, "name": "x"}).name == "x"


@pytest.mark.parametrize("version", [2, "1", None])
def test_parse_document_rejects_unsupported_version(version: object) -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_document(Example, {"schema_version": version, "name": "x"})
    assert caught.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
    assert caught.value.details == {"schema_version": version, "supported": [1]}


def test_parse_document_rejects_missing_version() -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_document(Example, {"name": "x"})
    assert caught.value.details == {"schema_version": None, "supported": [1]}


def test_parse_document_rejects_unknown_field() -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_document(Example, {"schema_version": 1, "name": "x", "surprise": True})
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT
    errors: list[dict[str, Any]] = caught.value.details["errors"]
    assert errors[0]["loc"] == ["surprise"]


def test_parse_document_rejects_non_mapping() -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_document(Example, ["not", "a", "mapping"])
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT


def test_error_info_is_json_ready() -> None:
    error = HyperWikiError(ErrorCode.NOT_FOUND, "missing", {"path": "x"})
    assert str(error) == "not_found: missing"
    assert error.to_info().model_dump(mode="json") == {
        "code": "not_found",
        "message": "missing",
        "details": {"path": "x"},
    }


@pytest.mark.parametrize("key", ["ABCD2345", "IT2ENG22"])
def test_zotero_key_accepts_valid(key: str) -> None:
    assert TypeAdapter(ZoteroKey).validate_python(key) == key


@pytest.mark.parametrize("key", ["abcd2345", "ABCD1234", "ABCDO234", "ABC", "ABCD23456"])
def test_zotero_key_rejects_invalid(key: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ZoteroKey).validate_python(key)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --locked pytest tests/test_errors_and_common.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hyperwiki.domain'`.

- [ ] **Step 4: Implement `hyperwiki.errors` and `hyperwiki.domain.common`** per the Interfaces block

`parse_document` checks, in order: `data` is a `Mapping` (else `INVALID_DOCUMENT`); `data.get("schema_version")` is an `int` (not `bool`) in `model.SUPPORTED_SCHEMA_VERSIONS` (else `UNSUPPORTED_SCHEMA_VERSION` with details `{"schema_version": <value or None>, "supported": sorted(...)}`); then `model.model_validate(data)`, converting `ValidationError` to `INVALID_DOCUMENT` with `details={"errors": json.loads(exc.json(include_url=False))}` so details stay JSON-serializable.

- [ ] **Step 5: Run tests and all checks**

Run: `uv run --locked pytest tests/test_errors_and_common.py -v` then the full check list from Global Constraints.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/hyperwiki/errors.py src/hyperwiki/domain tests/test_errors_and_common.py
git commit -m "feat: add structured errors and versioned contract base (HW-002)"
```

---

### Task 2: Zotero-shaped library manifest

**Files:**
- Create: `src/hyperwiki/domain/library.py`, `tests/manifest_factory.py`
- Test: `tests/test_contracts_library.py`

**Interfaces:**
- Consumes: `Contract`, `VersionedDocument`, `ZoteroKey`, `parse_document` (Task 1).
- Produces (`hyperwiki.domain.library`):
  - `Availability = Literal["available", "missing", "invalid_path", "remote"]`
  - `LinkMode = Literal["imported_file", "imported_url", "linked_file", "linked_url"]`
  - `AnnotationType = Literal["highlight", "underline", "note", "image", "ink", "text"]`
  - `class LibraryRef(Contract)`: `id: str` (min length 1), `kind: Literal["user", "group"]`, `name: str` (min length 1).
  - `class Collection(Contract)`: `key: ZoteroKey`, `version: int` (≥0), `name: str` (min 1), `parent_key: ZoteroKey | None`.
  - `class Creator(Contract)`: `creator_type: str`, `first_name: str | None = None`, `last_name: str | None = None`, `name: str | None = None`; valid only if `name` or `last_name` is set.
  - `class Item(Contract)`: `key`, `version`, `item_type: str`, `title: str`, `creators: list[Creator]`, `date: str | None`, `doi: str | None`, `url: str | None`, `abstract: str | None`, `language: str | None`, `tags: list[str]`, `collection_keys: list[ZoteroKey]`, `extra: dict[str, str] = {}`, `trashed: bool = False`, `date_modified: AwareDatetime`.
  - `class Attachment(Contract)`: `key`, `version`, `parent_key: ZoteroKey | None`, `content_type: str`, `link_mode: LinkMode`, `path: str | None`, `url: str | None`, `trashed: bool = False`, `date_modified: AwareDatetime`. `imported_file`, `imported_url`, and `linked_file` require `path`; `linked_url` requires `url` and forbids `path`.
  - `class Note(Contract)`: `key`, `version`, `parent_key: ZoteroKey | None`, `html: str`, `trashed: bool = False`, `date_modified: AwareDatetime`.
  - `class Annotation(Contract)`: `key`, `version`, `parent_key: ZoteroKey` (an attachment), `annotation_type: AnnotationType`, `text: str | None`, `comment: str | None`, `page_label: str | None`, `color: str | None`, `date_modified: AwareDatetime`.
  - `class LibraryManifest(VersionedDocument)`: `adapter: str`, `library: LibraryRef`, `snapshot_id: str` (min 1), `created_at: AwareDatetime`, `completeness: Literal["complete", "partial"]`, `storage_root: str = "storage"`, `collections`, `items`, `attachments`, `notes`, `annotations` (lists of the models above).
  - Manifest-level validation (a `model_validator(mode="after")` raising `ValueError`): keys unique within each entity list; attachment and note `parent_key` is `None` or an item key in the manifest; annotation `parent_key` is an attachment key in the manifest; every `collection_keys` entry and collection `parent_key` names a collection in the manifest.
- Produces (`tests/manifest_factory.py`): `NOW: str = "2026-11-02T00:00:00Z"` and `def minimal_manifest() -> dict[str, Any]` returning the dict below.

- [ ] **Step 1: Write the factory and failing tests**

`tests/manifest_factory.py`:

```python
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
```

`tests/test_contracts_library.py` (each "invalid" case asserts `HyperWikiError` with `ErrorCode.INVALID_DOCUMENT` via `parse_document(LibraryManifest, data)`):

```python
def test_minimal_manifest_parses() -> None:
    manifest = parse_document(LibraryManifest, minimal_manifest())
    assert manifest.items[0].key == "IT2ENG22"
    assert manifest.annotations[0].parent_key == "AT2ENG22"


# test_naive_datetime_rejected: created_at = "2026-11-02T00:00:00" -> invalid
# test_duplicate_keys_rejected: items = items * 2 -> invalid
# test_unknown_attachment_parent_rejected: attachments[0]["parent_key"] = "IT2ZZZ22" -> invalid
# test_standalone_attachment_and_note_allowed: both parent_key = None and annotations = [] -> parses
# test_annotation_parent_must_be_attachment: annotations[0]["parent_key"] = "IT2ENG22" -> invalid
# test_item_collection_must_exist: items[0]["collection_keys"] = ["CL2ZZZZ2"] -> invalid
# test_collection_parent_must_exist: collections[0]["parent_key"] = "CL2ZZZZ2" -> invalid
# test_imported_attachment_requires_path: attachments[0]["path"] = None -> invalid
# test_linked_url_requires_url_and_no_path:
#     link_mode "linked_url", path "x", url None -> invalid;
#     link_mode "linked_url", path None, url "https://example.org/p" -> parses
# test_creator_requires_a_name: creators = [{"creator_type": "author"}] -> invalid


def test_chinese_text_round_trips() -> None:
    data = minimal_manifest()
    data["items"][0]["title"] = "注意力机制综述"
    data["items"][0]["tags"] = ["深度学习"]
    manifest = parse_document(LibraryManifest, data)
    again = parse_document(LibraryManifest, json.loads(manifest.model_dump_json()))
    assert again == manifest
    assert again.items[0].title == "注意力机制综述"
```

Write each commented case as its own test function with the stated mutation and assertion.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --locked pytest tests/test_contracts_library.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hyperwiki.domain.library'`.

- [ ] **Step 3: Implement `hyperwiki.domain.library`** per the Interfaces block (field validators for per-entity rules; one manifest-level validator for cross-references).

- [ ] **Step 4: Run tests and all checks**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/hyperwiki/domain/library.py tests/manifest_factory.py tests/test_contracts_library.py
git commit -m "feat: add Zotero-shaped library manifest contract (HW-002)"
```

---

### Task 3: Change, evidence, scope, and retrieval contracts

**Files:**
- Create: `src/hyperwiki/domain/changes.py`, `src/hyperwiki/domain/evidence.py`, `src/hyperwiki/domain/scope.py`, `src/hyperwiki/domain/retrieval.py`
- Test: `tests/test_contracts_query.py`

**Interfaces:**
- Consumes: Task 1 base types.
- Produces:
  - `hyperwiki.domain.changes`: `ChangeKind = Literal["upsert", "trash", "restore", "delete"]`; `class ChangeEvent(Contract)`: `entity: EntityKind`, `key: ZoteroKey`, `change: ChangeKind`, `version: int` (≥0), `observed_at: AwareDatetime`.
  - `hyperwiki.domain.evidence`:
    - `EvidenceId = Annotated[str, StringConstraints(pattern=r"^ev_[0-9a-f]{32}$")]`
    - `LocatorPrecision = Literal["page", "section", "span", "bbox"]`
    - `class BoundingBox(Contract)`: `x0, y0, x1, y1: float`, requiring `x0 <= x1` and `y0 <= y1`.
    - `class EvidenceLocator(Contract)`: `attachment_key: ZoteroKey`, `attachment_sha256: Sha256Hex`, `extractor: str` (min 1, e.g. `"pypdf@5.1.0"`), `precision: LocatorPrecision`, `page_index: int | None = None` (≥0), `page_label: str | None = None`, `section: str | None = None`, `char_start: int | None = None`, `char_end: int | None = None`, `bbox: BoundingBox | None = None`. Rules: `char_start` and `char_end` both set or both unset, with `0 <= char_start < char_end`; precision `page` requires `page_index`; `section` requires `section`; `span` requires both offsets; `bbox` requires `bbox` and `page_index`.
    - `def make_evidence_id(locator: EvidenceLocator) -> EvidenceId`: `"ev_" + sha256(canonical)[:32]` where `canonical = json.dumps(locator.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.
  - `hyperwiki.domain.scope`: `class Scope(Contract)`: `item_keys: frozenset[ZoteroKey] = frozenset()`, `collection_keys: frozenset[ZoteroKey] = frozenset()`, `tags: frozenset[str] = frozenset()`, `year_from: int | None = None`, `year_to: int | None = None`; requires `year_from <= year_to` when both are set; property `is_whole_library -> bool` (true when every filter is empty or unset). Matching semantics (implemented in M1): an item is in scope when it matches every non-empty filter kind, any value within a kind.
  - `hyperwiki.domain.retrieval`:
    - `RetrievalMode = Literal["source", "wiki", "hybrid", "relations"]`
    - `class RetrievalRequest(Contract)`: `query: str` (min 1), `scope: Scope = Scope()`, `mode: RetrievalMode = "hybrid"`, `max_results: int = 10` (1–100), `evidence_token_budget: int = 8000` (≥1).
    - `class RetrievalHit(Contract)`: `evidence_id: EvidenceId`, `item_key: ZoteroKey`, `locator: EvidenceLocator`, `text: str`, `score: float`, `signals: dict[str, float] = {}`; requires `evidence_id == make_evidence_id(locator)`.
    - `class RetrievalResult(VersionedDocument)`: `request: RetrievalRequest`, `hits: list[RetrievalHit]`, `truncated: bool`, `warnings: list[str] = []`.

- [ ] **Step 1: Write the failing tests** in `tests/test_contracts_query.py`

```python
SHA = "a" * 64


def page_locator(**overrides: Any) -> dict[str, Any]:
    return {
        "attachment_key": "AT2ENG22",
        "attachment_sha256": SHA,
        "extractor": "test@1",
        "precision": "page",
        "page_index": 0,
    } | overrides


def test_change_event_parses() -> None:
    event = ChangeEvent.model_validate(
        {
            "entity": "item",
            "key": "IT2ENG22",
            "change": "trash",
            "version": 3,
            "observed_at": "2026-11-02T00:00:00Z",
        }
    )
    assert event.change == "trash"


# test_change_event_rejects_unknown_change: change "modify" -> pydantic ValidationError
# test_page_precision_requires_page_index: page_locator(page_index=None) -> ValidationError
# test_span_offsets_must_be_ordered: precision "span", char_start 10, char_end 5 -> ValidationError
# test_span_offsets_must_be_paired: precision "span", char_start 10 only -> ValidationError


def test_evidence_id_is_stable_and_versioned() -> None:
    first = make_evidence_id(EvidenceLocator.model_validate(page_locator()))
    assert first == make_evidence_id(EvidenceLocator.model_validate(page_locator()))
    assert re.fullmatch(r"ev_[0-9a-f]{32}", first)
    assert first != make_evidence_id(
        EvidenceLocator.model_validate(page_locator(extractor="test@2"))
    )
    assert first != make_evidence_id(
        EvidenceLocator.model_validate(page_locator(attachment_sha256="b" * 64))
    )


# test_default_scope_is_whole_library: Scope().is_whole_library is True
# test_scope_with_collection_is_not_whole_library: Scope(collection_keys=frozenset({"CL2DEEP2"})) -> False
# test_year_range_must_be_ordered: Scope(year_from=2025, year_to=2020) -> ValidationError
# test_retrieval_request_rejects_empty_query: RetrievalRequest(query="") -> ValidationError


def test_retrieval_result_requires_consistent_evidence_ids() -> None:
    locator = page_locator()
    good_id = make_evidence_id(EvidenceLocator.model_validate(locator))
    hit = {
        "evidence_id": good_id,
        "item_key": "IT2ENG22",
        "locator": locator,
        "text": "注意力",
        "score": 1.0,
    }
    data = {
        "schema_version": 1,
        "request": {"query": "attention"},
        "hits": [hit],
        "truncated": False,
    }
    assert parse_document(RetrievalResult, data).hits[0].text == "注意力"
    data["hits"] = [hit | {"evidence_id": "ev_" + "0" * 32}]
    with pytest.raises(HyperWikiError) as caught:
        parse_document(RetrievalResult, data)
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT
```

Write each commented case as its own test function.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --locked pytest tests/test_contracts_query.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the four modules** per the Interfaces block.

- [ ] **Step 4: Run tests and all checks**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/hyperwiki/domain/changes.py src/hyperwiki/domain/evidence.py src/hyperwiki/domain/scope.py src/hyperwiki/domain/retrieval.py tests/test_contracts_query.py
git commit -m "feat: add change, evidence, scope, and retrieval contracts (HW-002)"
```

---

### Task 4: Wiki frontmatter contract

**Files:**
- Create: `src/hyperwiki/domain/wiki.py`
- Test: `tests/test_contracts_wiki.py`

**Interfaces:**
- Consumes: `VersionedDocument`, `ZoteroKey`, `Sha256Hex`, `ProviderKind` (Task 1); `EvidenceId` (Task 3).
- Produces (`hyperwiki.domain.wiki`):
  - `PageType = Literal["paper", "concept", "topic", "synthesis", "index", "note"]`
  - `PageStatus = Literal["current", "stale", "pending"]`
  - `WikiLanguage = Literal["en", "zh"]`
  - `class GeneratorFingerprint(Contract)`: `provider_kind: ProviderKind`, `model: str` (min 1), `prompt_version: str` (min 1).
  - `class WikiFrontmatter(VersionedDocument)`: `id: UUID`, `page_type: PageType`, `title: str` (min 1), `language: WikiLanguage`, `source_item_keys: list[ZoteroKey] = []`, `evidence_ids: list[EvidenceId] = []`, `revision: int` (≥1), `status: PageStatus`, `generated_by: GeneratorFingerprint | None = None`, `generated_sha256: Sha256Hex | None = None`, `last_verified_at: AwareDatetime | None = None`. Rules: `page_type == "note"` forbids `generated_by` and `generated_sha256` (personal notes are never generated); any other page type with `status != "pending"` requires both.

- [ ] **Step 1: Write the failing tests** in `tests/test_contracts_wiki.py`

```python
def paper_page(**overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "3f2b9c1e-0000-4000-8000-000000000001",
        "page_type": "paper",
        "title": "基于注意力机制的合成实验研究",
        "language": "zh",
        "source_item_keys": ["IT2ZHN22"],
        "evidence_ids": ["ev_" + "1" * 32],
        "revision": 1,
        "status": "current",
        "generated_by": {
            "provider_kind": "local",
            "model": "qwen3:8b",
            "prompt_version": "paper-v1",
        },
        "generated_sha256": "c" * 64,
        "last_verified_at": "2026-11-02T00:00:00Z",
    } | overrides


def test_generated_paper_page_parses() -> None:
    page = parse_document(WikiFrontmatter, paper_page())
    assert page.generated_by is not None and page.generated_by.provider_kind == "local"


# test_personal_note_cannot_have_generator: page_type "note" with generated_by -> INVALID_DOCUMENT
# test_personal_note_without_generator_parses: page_type "note", generated_by None, generated_sha256 None -> parses
# test_current_generated_page_requires_fingerprint_and_hash: generated_by None -> INVALID_DOCUMENT
# test_pending_page_may_lack_generator: status "pending", generated_by None, generated_sha256 None -> parses
# test_language_is_restricted: language "fr" -> INVALID_DOCUMENT
# test_revision_starts_at_one: revision 0 -> INVALID_DOCUMENT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --locked pytest tests/test_contracts_wiki.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `hyperwiki.domain.wiki`** per the Interfaces block. YAML reading and writing of frontmatter is M2; this task defines and validates the model only.

- [ ] **Step 4: Run tests and all checks**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/hyperwiki/domain/wiki.py tests/test_contracts_wiki.py
git commit -m "feat: add wiki frontmatter contract (HW-002)"
```

---

### Task 5: Bilingual synthetic fixtures and lifecycle mutations

**Files:**
- Create: `.gitattributes`, `tests/fixtures/libraries/bilingual/manifest.json`, `tests/fixtures/libraries/bilingual/checksums.json`, `tests/fixtures/libraries/bilingual/storage/AT2ENG22/attention-study.txt`, `tests/fixtures/libraries/bilingual/storage/AT2ZHN22/中文论文.txt`, `tests/fixtures/README.md`, `tests/conftest.py`, `tests/snapshot_mutations.py`
- Test: `tests/test_fixtures.py`

**Interfaces:**
- Consumes: `LibraryManifest`, `parse_document` (Tasks 1–2).
- Produces:
  - `tests/conftest.py`: fixture `bilingual_snapshot(tmp_path: Path) -> Path` copying `BILINGUAL_DIR` to `tmp_path / "bilingual"` and returning it.
  - `tests/snapshot_mutations.py` exports `FIXTURES_DIR = Path(__file__).parent / "fixtures"` and `BILINGUAL_DIR = FIXTURES_DIR / "libraries" / "bilingual"` (test modules import these from here, never from `conftest`), plus the mutation helpers below. Each mutation bumps the touched entity's `version` to the manifest's maximum entity version plus one and sets `date_modified` to `"2026-11-03T00:00:00Z"`):
    - `load_manifest_dict(snapshot_dir: Path) -> dict[str, Any]`
    - `write_manifest_dict(snapshot_dir: Path, data: dict[str, Any]) -> None`
    - `edit_item_title(snapshot_dir: Path, key: str, title: str) -> None`
    - `replace_attachment_content(snapshot_dir: Path, key: str, content: bytes) -> None`
    - `set_trashed(snapshot_dir: Path, entity: Literal["item", "attachment", "note"], key: str, trashed: bool) -> None`
    - `delete_item(snapshot_dir: Path, key: str) -> None` (removes the item, its child attachments, notes, and their annotations, and each removed attachment's storage folder)
    - `make_partial(snapshot_dir: Path, collection_key: str) -> None` (keeps the collection's items, their children, every collection those items reference, and those collections' ancestors; sets `completeness` to `"partial"`)

Fixture content (synthetic, written for this project, no third-party text). Library: `adapter "snapshot"`, `library {"id": "synthetic-bilingual", "kind": "user", "name": "合成双语文库 Synthetic bilingual library"}`, `snapshot_id "bilingual-2026-11-02"`, `created_at "2026-11-02T00:00:00Z"`, `completeness "complete"`, `storage_root "storage"`. All entity versions 1 and `date_modified "2026-10-01T00:00:00Z"`.

| Kind | Key | Content |
|---|---|---|
| Collection | `CL2DEEP2` | "Deep Learning", no parent |
| Collection | `CL2ATTN2` | "注意力机制", parent `CL2DEEP2` |
| Collection | `CL2UNRD2` | "Unread", no parent |
| Item | `IT2ENG22` | journalArticle "A Synthetic Study of Attention Methods", en, author Ada Example, date "2025-03-14", DOI `10.0000/hw.synthetic.1`, tags `attention`, `synthetic`, in `CL2ATTN2` |
| Item | `IT2ZHN22` | journalArticle "基于注意力机制的合成实验研究", zh, author name "张三", date "2024年5月", tags `注意力`, `合成数据`, in `CL2ATTN2` |
| Item | `IT2MIX22` | conferencePaper "Cross-lingual Retrieval 跨语言检索: A Synthetic Benchmark", en, date "2023", tag `retrieval`, in `CL2DEEP2` |
| Item | `IT2DUP22` | duplicate of `IT2ENG22` (same title and DOI), in `CL2UNRD2`, no tags |
| Item | `IT2NAT22` | report "Notes Without Attachments", unfiled |
| Item | `IT2TRS22` | journalArticle "A Trashed Synthetic Paper", `trashed: true`, in `CL2UNRD2` |
| Attachment | `AT2ENG22` | parent `IT2ENG22`, imported_file, text/plain, `AT2ENG22/attention-study.txt` (two short English paragraphs mentioning "attention weights are normalized") |
| Attachment | `AT2ZHN22` | parent `IT2ZHN22`, imported_file, text/plain, `AT2ZHN22/中文论文.txt` (two short Chinese paragraphs including "注意力权重经过归一化") |
| Attachment | `AT2MIS22` | parent `IT2MIX22`, imported_file, application/pdf, `AT2MIS22/missing.pdf` (file intentionally absent) |
| Attachment | `AT2URL22` | standalone, linked_url, text/html, url `https://example.org/synthetic-preprint` |
| Note | `NT2ENG22` | parent `IT2ENG22`, `<p>My note: compare with the Chinese study.</p>` |
| Note | `NT2STA22` | standalone, `<p>独立笔记：待读清单。</p>` |
| Annotation | `AN2ENG22` | parent `AT2ENG22`, highlight "attention weights are normalized", comment "key definition", page label "1", color `#ffd400` |
| Annotation | `AN2ZHN22` | parent `AT2ZHN22`, highlight "注意力权重经过归一化", no comment, page label "2", color `#ffd400` |

`.gitattributes`:

```text
* text=auto eol=lf
tests/fixtures/**/storage/** binary
```

`checksums.json` maps each storage-relative path (POSIX separators) to its SHA-256, computed after writing the files. `tests/fixtures/README.md` states that all fixture content is synthetic and written for this project.

- [ ] **Step 1: Write the failing tests** in `tests/test_fixtures.py`

```python
BILINGUAL = BILINGUAL_DIR


def load(snapshot_dir: Path) -> LibraryManifest:
    return parse_document(LibraryManifest, load_manifest_dict(snapshot_dir))


def test_bilingual_fixture_parses() -> None:
    manifest = load(BILINGUAL)
    assert (
        len(manifest.collections),
        len(manifest.items),
        len(manifest.attachments),
        len(manifest.notes),
        len(manifest.annotations),
    ) == (3, 6, 4, 2, 2)
    assert sum(item.trashed for item in manifest.items) == 1


def test_fixture_checksums_match() -> None:
    expected: dict[str, str] = json.loads(
        (BILINGUAL / "checksums.json").read_text(encoding="utf-8")
    )
    storage = BILINGUAL / "storage"
    actual = {
        p.relative_to(storage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in storage.rglob("*")
        if p.is_file()
    }
    assert actual == expected


def test_missing_pdf_is_absent() -> None:
    assert not (BILINGUAL / "storage" / "AT2MIS22" / "missing.pdf").exists()


# test_edit_item_title: edit_item_title(snap, "IT2ENG22", "Revised") -> title "Revised", version 2
# test_replace_attachment_content: replace_attachment_content(snap, "AT2ENG22", b"new") ->
#     file bytes == b"new", attachment version 2
# test_trash_and_restore: set_trashed(snap, "item", "IT2ENG22", True) -> trashed; then False -> not trashed
# test_delete_item_removes_children: delete_item(snap, "IT2ENG22") -> no IT2ENG22, AT2ENG22, NT2ENG22,
#     AN2ENG22 in manifest; storage/AT2ENG22 does not exist; manifest still parses
# test_make_partial: make_partial(snap, "CL2ATTN2") -> completeness "partial";
#     counts (collections, items, attachments, notes, annotations) == (2, 2, 2, 1, 2)
```

Every mutation test takes the `bilingual_snapshot` fixture and ends by parsing the manifest with `load`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --locked pytest tests/test_fixtures.py -v`
Expected: FAIL (fixture files and `snapshot_mutations` do not exist yet).

- [ ] **Step 3: Write the fixture files, `.gitattributes`, `conftest.py`, and `snapshot_mutations.py`**, then compute and record `checksums.json`.

- [ ] **Step 4: Run tests and all checks**

Expected: all pass. Run `git check-attr -a "tests/fixtures/libraries/bilingual/storage/AT2ZHN22/中文论文.txt"`; expected output includes `binary: set`.

- [ ] **Step 5: Commit**

```bash
git add .gitattributes tests/fixtures tests/conftest.py tests/snapshot_mutations.py tests/test_fixtures.py
git commit -m "test: add bilingual synthetic library fixtures and mutations (HW-003)"
```

---

### Task 6: SQLite storage and migrations

**Files:**
- Create: `src/hyperwiki/storage/__init__.py`, `src/hyperwiki/storage/database.py`, `src/hyperwiki/storage/migrations/0001_initial.sql`, `src/hyperwiki/storage/library_repo.py`
- Modify: `tests/conftest.py` (add `database` fixture)
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: library models and `Availability` (Task 2); `EntityKind`, `Contract` (Task 1); `HyperWikiError` (Task 1).
- Produces (`hyperwiki.storage.database`):
  - `class Migration(NamedTuple)`: `version: int`, `name: str`, `sql: str`.
  - `MIGRATIONS: tuple[Migration, ...]` loaded with `importlib.resources` from `hyperwiki/storage/migrations/NNNN_name.sql`, ordered by version.
  - `def latest_version(migrations: Sequence[Migration] = MIGRATIONS) -> int`
  - `def connect(path: Path) -> sqlite3.Connection`: creates the parent directory; `PRAGMA foreign_keys = ON`; `PRAGMA journal_mode = WAL`; `row_factory = sqlite3.Row`.
  - `def migrate(conn: sqlite3.Connection, migrations: Sequence[Migration] = MIGRATIONS) -> int`: raises `HyperWikiError(UNSUPPORTED_DATABASE_VERSION, …, {"found": n, "latest": m})` when `PRAGMA user_version` exceeds the latest; otherwise applies each pending migration in its own transaction, sets `user_version`, and returns the resulting version.
  - `def open_database(path: Path) -> sqlite3.Connection`: `connect` then `migrate`; closes the connection before re-raising on failure.
- Produces (`hyperwiki.storage.library_repo`):
  - `LIBRARY_NAMESPACE = uuid.UUID("0b6f3c1e-8d2a-4f5b-9c7e-2a1d4e6f8b90")`
  - `def library_id_for(adapter: str, library: LibraryRef) -> str` → `str(uuid5(LIBRARY_NAMESPACE, f"{adapter}/{library.kind}/{library.id}"))`
  - `def stable_id(library_id: str, entity: EntityKind, key: str) -> str` → `str(uuid5(LIBRARY_NAMESPACE, f"{library_id}/{entity}/{key}"))`
  - `def upsert_library(conn: sqlite3.Connection, adapter: str, library: LibraryRef) -> str` (returns the library ID)
  - `def record_snapshot(conn: sqlite3.Connection, library_id: str, manifest: LibraryManifest, imported_at: datetime) -> None` (re-recording the same `snapshot_id` updates `imported_at`)
  - `def upsert_collection(conn, library_id: str, collection: Collection) -> None`
  - `def upsert_item(conn, library_id: str, item: Item) -> None` (replaces the item's tag and collection links)
  - `def upsert_attachment(conn, library_id: str, attachment: Attachment, *, availability: Availability, sha256: str | None, size_bytes: int | None) -> None`
  - `def upsert_note(conn, library_id: str, note: Note) -> None`
  - `def upsert_annotation(conn, library_id: str, annotation: Annotation) -> None`
  - `class EntityCounts(Contract)`: `collections: int`, `items: int`, `items_trashed: int`, `attachments: int`, `attachments_by_availability: dict[Availability, int]` (all four keys present, zeros included), `notes: int`, `annotations: int`.
  - `def count_entities(conn: sqlite3.Connection, library_id: str) -> EntityCounts`
- Schema `0001_initial.sql`: tables `libraries`, `snapshots`, `collections`, `items`, `item_collections`, `item_tags`, `attachments`, `notes`, `annotations`. Every entity table has `id TEXT PRIMARY KEY`, `library_id TEXT NOT NULL REFERENCES libraries(id)`, `key TEXT NOT NULL`, `version INTEGER NOT NULL`, and `UNIQUE (library_id, key)`. `items.data_json` stores the full item JSON. `attachments.parent_item_id`, `notes.parent_item_id`, and `annotations.attachment_id` are foreign keys; `attachments.availability` has `CHECK (availability IN ('available', 'missing', 'invalid_path', 'remote'))`; `snapshots.completeness` has `CHECK (completeness IN ('complete', 'partial'))`. Collection parents are stored as `parent_key` text.

- [ ] **Step 1: Add the `database` fixture and write the failing tests**

In `tests/conftest.py`:

```python
@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_database(tmp_path / "workspace" / "hyperwiki.sqlite3")
    try:
        yield conn
    finally:
        conn.close()
```

`tests/test_storage.py`:

```python
def test_open_database_creates_latest_schema(database: sqlite3.Connection) -> None:
    assert database.execute("PRAGMA user_version").fetchone()[0] == latest_version() == 1
    tables = {
        row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "libraries",
        "snapshots",
        "collections",
        "items",
        "item_collections",
        "item_tags",
        "attachments",
        "notes",
        "annotations",
    } <= tables


def test_migrations_apply_in_order(tmp_path: Path) -> None:
    extra = Migration(2, "extra", "CREATE TABLE extra (x INTEGER);")
    conn = connect(tmp_path / "db.sqlite3")
    try:
        assert migrate(conn, (*MIGRATIONS, extra)) == 2
        assert migrate(conn, (*MIGRATIONS, extra)) == 2
    finally:
        conn.close()


def test_newer_database_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    conn = connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(HyperWikiError) as caught:
        open_database(path)
    assert caught.value.code is ErrorCode.UNSUPPORTED_DATABASE_VERSION
    assert caught.value.details == {"found": 99, "latest": 1}


# test_foreign_keys_enforced: INSERT into annotations referencing a nonexistent attachment_id
#     raises sqlite3.IntegrityError
# test_stable_id_is_deterministic: stable_id("L", "item", "IT2ENG22") equal across calls,
#     different for library "M"
# test_upsert_item_is_idempotent: upsert the minimal manifest's library, collection, and item twice;
#     SELECT COUNT(*) FROM items == 1 and the id equals stable_id(library_id, "item", "IT2ENG22")
# test_upsert_item_replaces_tags_and_collections: upsert with tags ["a"], then with tags ["b"] and
#     collection_keys [] -> item_tags holds only "b"; item_collections empty
# test_chinese_text_round_trips_through_database: item title "注意力机制综述" -> stored data_json
#     parsed back gives the same title
# test_count_entities: after upserting the minimal manifest -> EntityCounts(collections=1, items=1,
#     items_trashed=0, attachments=1, attachments_by_availability={"available": 1, "missing": 0,
#     "invalid_path": 0, "remote": 0}, notes=1, annotations=1)
```

Build manifests with `parse_document(LibraryManifest, minimal_manifest())`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --locked pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hyperwiki.storage'`.

- [ ] **Step 3: Implement the schema, `database.py`, and `library_repo.py`** per the Interfaces block (upserts use `INSERT … ON CONFLICT (library_id, key) DO UPDATE`).

- [ ] **Step 4: Run tests and all checks; confirm the migration ships in the wheel**

Run: the full check list, then `uv build --out-dir "$TMPDIR/hw-wheel" && unzip -l "$TMPDIR/hw-wheel"/hyperwiki-*.whl | grep 0001_initial.sql`
Expected: all pass; the grep prints the migration path.

- [ ] **Step 5: Commit**

```bash
git add src/hyperwiki/storage tests/conftest.py tests/test_storage.py
git commit -m "feat: add SQLite storage, migrations, and library repository (HW-004)"
```

---

### Task 7: Read-only snapshot adapter

**Files:**
- Create: `src/hyperwiki/adapters/__init__.py`, `src/hyperwiki/adapters/snapshot.py`
- Test: `tests/test_snapshot_adapter.py`

**Interfaces:**
- Consumes: `LibraryManifest`, `Availability` (Task 2); `Problem`, `parse_document`, `Sha256Hex`, `ZoteroKey` (Task 1).
- Produces (`hyperwiki.adapters.snapshot`):
  - `MANIFEST_NAME = "manifest.json"`
  - `class AttachmentFile(Contract)`: `key: ZoteroKey`, `availability: Availability`, `resolved_path: Path | None`, `sha256: Sha256Hex | None`, `size_bytes: int | None`.
  - `class LoadedSnapshot(Contract)`: `directory: Path`, `manifest: LibraryManifest`, `files: dict[str, AttachmentFile]` (keyed by attachment key, one entry per attachment), `problems: list[Problem]` (sorted by entity, then key).
  - `def load_snapshot(snapshot_dir: Path, *, linked_file_roots: Sequence[Path] = ()) -> LoadedSnapshot`

Rules:
- Missing `manifest.json` → `HyperWikiError(NOT_FOUND)`. Invalid JSON → `HyperWikiError(INVALID_DOCUMENT)` with `details={"line": …, "column": …}`. Then `parse_document`.
- `storage_root` must be a relative POSIX path without `..` that resolves inside `snapshot_dir`; otherwise `HyperWikiError(INVALID_PATH)` (fatal).
- `linked_url` → `remote`, no file access.
- `imported_file` / `imported_url`: `path` must be relative, use `/` separators only, contain no `..` segment, no backslash, and no drive prefix (`^[A-Za-z]:`); the resolved path (after `Path.resolve()`, so symlinks are followed) must lie inside the resolved storage root. Violations → `invalid_path` plus a `Problem(code=INVALID_PATH, entity="attachment", …)`; the file is never opened.
- `linked_file`: `path` must be absolute and, once resolved, inside one of the resolved `linked_file_roots`; otherwise `invalid_path` with a problem (no roots means every linked file is reported).
- A permitted path that does not exist → `missing` plus `Problem(code=NOT_FOUND, …)`. An existing file → `available` with streamed SHA-256 (1 MiB chunks) and size.
- Nothing is created, modified, or deleted under `snapshot_dir`.

- [ ] **Step 1: Write the failing tests** in `tests/test_snapshot_adapter.py`

```python
def test_loads_bilingual_fixture(bilingual_snapshot: Path) -> None:
    loaded = load_snapshot(bilingual_snapshot)
    checksums = json.loads((bilingual_snapshot / "checksums.json").read_text(encoding="utf-8"))
    assert loaded.files["AT2ENG22"].availability == "available"
    assert loaded.files["AT2ENG22"].sha256 == checksums["AT2ENG22/attention-study.txt"]
    assert loaded.files["AT2MIS22"].availability == "missing"
    assert loaded.files["AT2URL22"].availability == "remote"
    assert [(p.code, p.entity, p.key) for p in loaded.problems] == [
        (ErrorCode.NOT_FOUND, "attachment", "AT2MIS22")
    ]


def test_chinese_filename_resolves(bilingual_snapshot: Path) -> None:
    assert load_snapshot(bilingual_snapshot).files["AT2ZHN22"].availability == "available"


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.txt",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "AT2ENG22\\attention-study.txt",
        "AT2ENG22/../../outside.txt",
    ],
)
def test_path_escape_is_reported_not_read(bilingual_snapshot: Path, bad_path: str) -> None:
    data = load_manifest_dict(bilingual_snapshot)
    attachment = next(a for a in data["attachments"] if a["key"] == "AT2ENG22")
    attachment["path"] = bad_path
    write_manifest_dict(bilingual_snapshot, data)
    loaded = load_snapshot(bilingual_snapshot)
    assert loaded.files["AT2ENG22"].availability == "invalid_path"
    assert loaded.files["AT2ENG22"].sha256 is None
    assert (ErrorCode.INVALID_PATH, "AT2ENG22") in [(p.code, p.key) for p in loaded.problems]


# test_symlink_escaping_storage_is_invalid: create storage/AT2ENG22/link.txt -> a file outside the
#     snapshot (pytest.skip if os.symlink raises OSError, e.g. on Windows without privilege); point
#     AT2ENG22 at it -> "invalid_path"
# test_linked_file_roots: AT2ENG22 as linked_file with an absolute path to a file in tmp_path/"papers";
#     without roots -> "invalid_path"; with linked_file_roots=[tmp_path / "papers"] -> "available"
# test_missing_manifest_is_not_found: empty directory -> HyperWikiError NOT_FOUND
# test_malformed_json_is_invalid_document: manifest text "{" -> INVALID_DOCUMENT with "line" in details
# test_storage_root_must_stay_inside_snapshot: storage_root "../elsewhere" -> HyperWikiError INVALID_PATH


def test_load_does_not_modify_snapshot_directory(bilingual_snapshot: Path) -> None:
    def state() -> list[tuple[str, int, int]]:
        return sorted(
            (p.relative_to(bilingual_snapshot).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
            for p in bilingual_snapshot.rglob("*")
        )

    before = state()
    load_snapshot(bilingual_snapshot)
    assert state() == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --locked pytest tests/test_snapshot_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hyperwiki.adapters'`.

- [ ] **Step 3: Implement `hyperwiki.adapters.snapshot`** per the Interfaces block and rules.

- [ ] **Step 4: Run tests and all checks**

Expected: all pass (the symlink case may report as skipped on Windows only).

- [ ] **Step 5: Commit**

```bash
git add src/hyperwiki/adapters tests/test_snapshot_adapter.py
git commit -m "feat: add read-only snapshot adapter with path safety (HW-005)"
```

---

### Task 8: Import service and JSON CLI

**Files:**
- Create: `src/hyperwiki/services/__init__.py`, `src/hyperwiki/services/library_import.py`, `src/hyperwiki/interfaces/__init__.py`, `src/hyperwiki/interfaces/cli.py`
- Modify: `pyproject.toml` (add `[project.scripts] hyperwiki = "hyperwiki.interfaces.cli:entrypoint"`), `README.md` (Development section: example `import` and `status` commands)
- Test: `tests/test_library_import.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `load_snapshot`, `LoadedSnapshot` (Task 7); repository functions and `EntityCounts` (Task 6); `open_database` (Task 6); `HyperWikiError`, `Problem` (Task 1).
- Produces (`hyperwiki.services.library_import`):
  - `class ImportReport(Contract)`: `library_id: str`, `snapshot_id: str`, `completeness: Literal["complete", "partial"]`, `counts: EntityCounts`, `problems: list[Problem]`.
  - `class LibraryStatus(Contract)`: `library_id: str`, `adapter: str`, `name: str`, `last_snapshot_id: str`, `last_completeness: Literal["complete", "partial"]`, `last_imported_at: AwareDatetime`, `counts: EntityCounts`.
  - `class StatusReport(Contract)`: `libraries: list[LibraryStatus]` (ordered by name).
  - `def import_snapshot(conn: sqlite3.Connection, snapshot_dir: Path, *, linked_file_roots: Sequence[Path] = (), now: Callable[[], datetime] = utc_now) -> ImportReport`: loads the snapshot, then in one transaction (`with conn:`) upserts library, snapshot, collections, items, attachments, notes, annotations in that order. M0 import only inserts and updates; it never deletes or tombstones (absence handling is M1's update engine).
  - `def library_status(conn: sqlite3.Connection) -> StatusReport`
  - `def utc_now() -> datetime`
- Produces (`hyperwiki.interfaces.cli`):
  - `def main(argv: Sequence[str] | None = None, *, stdout: BinaryIO | None = None, environ: Mapping[str, str] | None = None) -> int`
  - `def entrypoint() -> None` (`raise SystemExit(main())`)
  - Commands: `hyperwiki [--workspace DIR] import SNAPSHOT_DIR [--linked-root DIR ...]`, `hyperwiki [--workspace DIR] status`, `hyperwiki version`.
  - Workspace: `--workspace`, else `HYPERWIKI_WORKSPACE`, else `Path.home() / ".hyperwiki"`; database at `<workspace>/hyperwiki.sqlite3`.
  - Output: success `{"ok": true, "result": <model JSON>}`, exit 0; `HyperWikiError` → `{"ok": false, "error": <ErrorInfo JSON>}`, exit 1; usage errors are argparse's (exit 2). Written as `json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"` to `stdout` (default `sys.stdout.buffer`).

- [ ] **Step 1: Write the failing tests**

`tests/test_library_import.py`:

```python
def item_ids(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (row["key"], row["id"]) for row in conn.execute("SELECT key, id FROM items ORDER BY key")
    ]


def test_import_bilingual_counts(database: sqlite3.Connection, bilingual_snapshot: Path) -> None:
    report = import_snapshot(database, bilingual_snapshot)
    assert (
        report.counts.collections,
        report.counts.items,
        report.counts.items_trashed,
        report.counts.attachments,
        report.counts.notes,
        report.counts.annotations,
    ) == (3, 6, 1, 4, 2, 2)
    assert report.counts.attachments_by_availability == {
        "available": 2,
        "missing": 1,
        "invalid_path": 0,
        "remote": 1,
    }
    assert [(p.code, p.key) for p in report.problems] == [(ErrorCode.NOT_FOUND, "AT2MIS22")]


def test_reimport_creates_no_duplicates_and_keeps_ids(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    first = import_snapshot(database, bilingual_snapshot)
    ids = item_ids(database)
    second = import_snapshot(database, bilingual_snapshot)
    assert second.counts == first.counts
    assert item_ids(database) == ids


def test_partial_after_complete_deletes_nothing(
    database: sqlite3.Connection, bilingual_snapshot: Path, tmp_path: Path
) -> None:
    complete = import_snapshot(database, bilingual_snapshot)
    partial_dir = tmp_path / "partial"
    shutil.copytree(bilingual_snapshot, partial_dir)
    make_partial(partial_dir, "CL2ATTN2")
    data = load_manifest_dict(partial_dir)
    data["snapshot_id"] = "bilingual-partial"
    write_manifest_dict(partial_dir, data)
    report = import_snapshot(database, partial_dir)
    assert report.completeness == "partial"
    assert report.counts == complete.counts


def test_failed_import_leaves_no_partial_state(
    database: sqlite3.Connection, bilingual_snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected failure")

    monkeypatch.setattr("hyperwiki.services.library_import.upsert_note", boom)
    with pytest.raises(RuntimeError):
        import_snapshot(database, bilingual_snapshot)
    assert library_status(database).libraries == []


# test_status_reports_last_snapshot: after import, library_status(...).libraries[0] has
#     name "合成双语文库 Synthetic bilingual library", last_snapshot_id "bilingual-2026-11-02",
#     last_completeness "complete", counts equal to the import report's counts
```

`tests/test_cli.py`:

```python
def run_cli(
    args: list[str], environ: dict[str, str] | None = None
) -> tuple[int, dict[str, Any], bytes]:
    out = io.BytesIO()
    code = main(args, stdout=out, environ=environ or {})
    raw = out.getvalue()
    return code, json.loads(raw.decode("utf-8")), raw


def test_import_then_status(tmp_path: Path, bilingual_snapshot: Path) -> None:
    ws = str(tmp_path / "ws")
    code, payload, _ = run_cli(["--workspace", ws, "import", str(bilingual_snapshot)])
    assert code == 0 and payload["ok"] is True
    assert payload["result"]["counts"]["items"] == 6
    code, payload, _ = run_cli(["--workspace", ws, "status"])
    assert payload["result"]["libraries"][0]["last_snapshot_id"] == "bilingual-2026-11-02"


def test_output_is_utf8_json_with_chinese(tmp_path: Path, bilingual_snapshot: Path) -> None:
    ws = str(tmp_path / "ws")
    run_cli(["--workspace", ws, "import", str(bilingual_snapshot)])
    _, _, raw = run_cli(["--workspace", ws, "status"])
    assert "合成双语文库".encode("utf-8") in raw


# test_missing_snapshot_returns_error_envelope: import of tmp_path / "nope" -> exit 1,
#     payload["ok"] is False, payload["error"]["code"] == "not_found"
# test_workspace_from_environment: run_cli(["import", str(snap)], {"HYPERWIKI_WORKSPACE": str(tmp_path / "env-ws")})
#     -> (tmp_path / "env-ws" / "hyperwiki.sqlite3").exists()
# test_version_command: run_cli(["version"]) -> payload["result"] == {"version": hyperwiki.__version__}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --locked pytest tests/test_library_import.py tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the service and CLI** per the Interfaces block; add the console script and README examples. Import the repository functions into `hyperwiki.services.library_import` by name (`from hyperwiki.storage.library_repo import upsert_note, …`), because `test_failed_import_leaves_no_partial_state` patches `hyperwiki.services.library_import.upsert_note`.

- [ ] **Step 4: Run tests, all checks, and the installed command**

Run: the full check list; then `uv sync --locked && uv run hyperwiki --workspace "$TMPDIR/hw-ws" import tests/fixtures/libraries/bilingual` and `uv run hyperwiki --workspace "$TMPDIR/hw-ws" status`.
Expected: all pass; both commands print `{"ok": true, …}` with Chinese text readable.

- [ ] **Step 5: Commit**

```bash
git add src/hyperwiki/services src/hyperwiki/interfaces pyproject.toml uv.lock README.md tests/test_library_import.py tests/test_cli.py
git commit -m "feat: add snapshot import service and JSON CLI (HW-005)"
```

---

### Task 9: Provider contract, spending guard, fakes, and subscription queue

**Files:**
- Create: `src/hyperwiki/domain/providers.py`, `src/hyperwiki/providers/__init__.py`, `src/hyperwiki/providers/base.py`, `src/hyperwiki/providers/fake.py`, `src/hyperwiki/providers/spending.py`, `src/hyperwiki/providers/queue.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: `Contract`, `VersionedDocument`, `parse_document` (Task 1); `EvidenceId` (Task 3).
- Produces (`hyperwiki.domain.providers`):
  - `TaskName = Literal["extraction", "page_writing", "synthesis"]`
  - `class ApiProvider(Contract)`: `kind: Literal["api"]`, `base_url: HttpUrl`, `model: str` (min 1), `api_key_env: str` (pattern `^[A-Z_][A-Z0-9_]*$`), `daily_spend_cap_usd: float` (>0, required), `input_usd_per_million_tokens: float` (≥0), `output_usd_per_million_tokens: float` (≥0).
  - `class LocalProvider(Contract)`: `kind: Literal["local"]`, `base_url: HttpUrl`, `model: str` (min 1).
  - `class SubscriptionProvider(Contract)`: `kind: Literal["subscription"]`, `agent: Literal["claude-code", "codex", "any"] = "any"`.
  - `ProviderConfig = Annotated[ApiProvider | LocalProvider | SubscriptionProvider, Field(discriminator="kind")]`
  - `class ProviderSettings(VersionedDocument)`: `default: ProviderConfig`, `overrides: dict[TaskName, ProviderConfig] = {}`; method `for_task(self, task: TaskName) -> ApiProvider | LocalProvider | SubscriptionProvider`.
  - `def parse_provider_settings(data: object) -> ProviderSettings`: before `parse_document`, any provider mapping with `kind == "api"` lacking `daily_spend_cap_usd` → `HyperWikiError(SPENDING_CAP_REQUIRED)`; any provider mapping containing `api_key` → `HyperWikiError(INVALID_DOCUMENT, "store the key in an environment variable and reference it with api_key_env")`.
  - `AITaskKind = Literal["write_page", "revise_page", "extract_relations"]`; `AITaskStatus = Literal["pending", "done"]`
  - `class AITask(Contract)`: `task_id: UUID`, `kind: AITaskKind`, `target_id: str`, `input_evidence_ids: list[EvidenceId]`, `instructions: str`, `created_at: AwareDatetime`, `status: AITaskStatus = "pending"`.
  - `class DraftSubmission(Contract)`: `task_id: UUID`, `content_markdown: str`, `cited_evidence_ids: list[EvidenceId]`.
- Produces (`hyperwiki.providers`):
  - `base.py`: `class GenerationRequest(Contract)`: `task: TaskName`, `system: str`, `prompt: str`, `max_output_tokens: int` (>0). `class GenerationResult(Contract)`: `text: str`, `input_tokens: int`, `output_tokens: int`, `model: str`, `cost_usd: float`. `class TextGenerator(Protocol)`: `def generate(self, request: GenerationRequest) -> GenerationResult: ...`
  - `spending.py`: `def estimate_tokens(text: str) -> int` = `ceil(ascii_chars / 4) + non_ascii_chars` (conservative for Chinese); `def estimate_cost_usd(provider: ApiProvider, input_tokens: int, output_tokens: int) -> float`; `class SpendingGuard`: `__init__(self, cap_usd: float, spent_usd: float = 0.0)`, `check(self, estimated_cost_usd: float) -> None` (raises `SPENDING_CAP_REACHED` with details `{"cap_usd", "spent_usd", "estimated_usd"}` when `spent + estimated > cap`), `record(self, cost_usd: float) -> None`, property `remaining_usd -> float`. Daily reset and persistence arrive with the M2 runtime.
  - `fake.py`: `class FakeGenerator`: `__init__(self, responses: Mapping[str, str] | None = None, model: str = "fake-1")`; `generate` returns `responses[prompt]` if present, else `f"[fake:{task}] {sha256(prompt)[:12]}"`, tokens from `estimate_tokens`, `cost_usd=0.0`; attribute `calls: list[GenerationRequest]`.
  - `queue.py`: `class TaskQueue(Protocol)` with `enqueue(task: AITask) -> None`, `pending() -> list[AITask]`, `submit(draft: DraftSubmission) -> AITask`; `class InMemoryTaskQueue` implementing it. `submit` raises `NOT_FOUND` for an unknown or already-done task and `INVALID_DOCUMENT` (details `{"unknown_evidence_ids": [...]}`) when the draft cites evidence not in the task's inputs; otherwise returns the task with `status="done"`. Full citation validation against stored evidence is M2.

- [ ] **Step 1: Write the failing tests** in `tests/test_providers.py`

```python
API = {
    "kind": "api",
    "base_url": "https://api.example.com/v1",
    "model": "m",
    "api_key_env": "HW_TEST_KEY",
    "daily_spend_cap_usd": 1.0,
    "input_usd_per_million_tokens": 1.0,
    "output_usd_per_million_tokens": 2.0,
}


def test_api_provider_requires_spending_cap() -> None:
    api = {k: v for k, v in API.items() if k != "daily_spend_cap_usd"}
    with pytest.raises(HyperWikiError) as caught:
        parse_provider_settings({"schema_version": 1, "default": api})
    assert caught.value.code is ErrorCode.SPENDING_CAP_REQUIRED


def test_literal_api_key_is_rejected() -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_provider_settings({"schema_version": 1, "default": API | {"api_key": "sk-secret"}})
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT
    assert "sk-secret" not in str(caught.value)


def test_overrides_select_provider_for_task() -> None:
    settings = parse_provider_settings(
        {
            "schema_version": 1,
            "default": {"kind": "subscription"},
            "overrides": {
                "extraction": {
                    "kind": "local",
                    "base_url": "http://localhost:11434/v1",
                    "model": "qwen3:8b",
                }
            },
        }
    )
    assert settings.for_task("extraction").kind == "local"
    assert settings.for_task("synthesis").kind == "subscription"


@pytest.mark.parametrize(
    ("text", "expected"), [("abcd" * 10, 10), ("注意力机制", 5), ("ab注意", 3)]
)
def test_estimate_tokens(text: str, expected: int) -> None:
    assert estimate_tokens(text) == expected


def test_spending_guard() -> None:
    guard = SpendingGuard(cap_usd=1.0)
    guard.check(0.6)
    guard.record(0.6)
    assert guard.remaining_usd == pytest.approx(0.4)
    with pytest.raises(HyperWikiError) as caught:
        guard.check(0.5)
    assert caught.value.code is ErrorCode.SPENDING_CAP_REACHED


# test_fake_generator_is_deterministic: two generate() calls with the same request return equal results;
#     len(fake.calls) == 2; responses mapping overrides the default text
# test_queue_round_trip: enqueue a task citing ["ev_" + "1" * 32]; pending() lists it; submit a draft citing
#     the same id -> returned status "done" and pending() == []
# test_queue_rejects_unknown_evidence: draft citing ["ev_" + "2" * 32] -> INVALID_DOCUMENT with
#     details["unknown_evidence_ids"] == ["ev_" + "2" * 32]
# test_queue_rejects_unknown_task: draft for a fresh uuid4() -> NOT_FOUND
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --locked pytest tests/test_providers.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the modules** per the Interfaces block.

- [ ] **Step 4: Run tests and all checks**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/hyperwiki/domain/providers.py src/hyperwiki/providers tests/test_providers.py
git commit -m "feat: add provider contract, spending guard, fakes, and task queue (HW-009)"
```

---

### Task 10: OpenAI-compatible generator, live smoke test, and provider ADR

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (add `httpx`; register pytest marker `live`)
- Create: `src/hyperwiki/providers/openai_compat.py`, `docs/decisions/0003-provider-contract.md`
- Test: `tests/test_openai_compat.py`

ADR numbering: name the record `docs/decisions/0003-provider-contract.md` unless the HW-006 change-detection spike has already taken 0003; then use the next free number. Later documents refer to it as "the provider ADR".

**Interfaces:**
- Consumes: `ApiProvider`, `LocalProvider` (Task 9); `GenerationRequest`, `GenerationResult`, `SpendingGuard`, `estimate_tokens`, `estimate_cost_usd` (Task 9).
- Produces (`hyperwiki.providers.openai_compat`):
  - `class OpenAICompatibleGenerator`: `__init__(self, config: ApiProvider | LocalProvider, *, api_key: str | None = None, guard: SpendingGuard | None = None, client: httpx.Client | None = None, timeout_s: float = 120.0)`; raises `SPENDING_CAP_REQUIRED` if `config` is `ApiProvider` and `guard` is `None`; `generate(self, request: GenerationRequest) -> GenerationResult`.
  - `def generator_from_config(config: ApiProvider | LocalProvider, environ: Mapping[str, str] | None = None) -> OpenAICompatibleGenerator`: for `ApiProvider`, reads the key from `environ[config.api_key_env]` (missing → `PROVIDER_NOT_CONFIGURED` naming the variable, never a value) and builds `SpendingGuard(config.daily_spend_cap_usd)`.
  - Request: `POST {base_url}/chat/completions` with JSON `{"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "max_tokens": max_output_tokens}`; `Authorization: Bearer <key>` only when a key is set.
  - Before sending (API kind): `guard.check(estimate_cost_usd(config, estimate_tokens(system + prompt), max_output_tokens))`. After: tokens from `usage.prompt_tokens` / `usage.completion_tokens` (estimates if `usage` is absent); `cost_usd` from the API prices (0.0 for local); `guard.record(cost_usd)`.
  - Non-2xx responses, timeouts, and malformed bodies → `HyperWikiError(PROVIDER_ERROR, …, {"status_code": …})`; no message or detail ever contains the API key.

- [ ] **Step 1: Add the dependency and marker**

Run: `uv add httpx`; add `markers = ["live: calls a real provider; opt-in via HYPERWIKI_SMOKE_* environment variables"]` under `[tool.pytest.ini_options]`.
Expected: lock updated; all checks still pass.

- [ ] **Step 2: Write the failing tests** in `tests/test_openai_compat.py` (all use `httpx.Client(transport=httpx.MockTransport(handler))`)

```python
# test_sends_openai_compatible_request: handler captures the request -> path ends "/v1/chat/completions",
#     header Authorization == "Bearer test-key", JSON body model/messages/max_tokens as specified
# test_parses_response_and_computes_cost: response {"choices": [{"message": {"content": "注意力"}}],
#     "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}} with prices 1.0/2.0 and
#     SpendingGuard(cap_usd=10.0) -> text "注意力", cost_usd == 2.0, guard.remaining_usd == pytest.approx(8.0)
# test_spending_cap_blocks_before_request: SpendingGuard(cap_usd=0.000001); handler fails the test if
#     called -> SPENDING_CAP_REACHED
# test_http_error_does_not_leak_key: 401 response -> PROVIDER_ERROR, details["status_code"] == 401,
#     "test-key" not in str(error) or json.dumps(error.details)
# test_local_provider_sends_no_auth_header: LocalProvider -> request has no Authorization header,
#     cost_usd == 0.0
# test_missing_api_key_env_is_provider_not_configured: generator_from_config(API config, environ={}) ->
#     PROVIDER_NOT_CONFIGURED, details mention "HW_TEST_KEY"


@pytest.mark.live
@pytest.mark.skipif(
    "HYPERWIKI_SMOKE_BASE_URL" not in os.environ, reason="live provider not configured"
)
def test_live_openai_compatible_smoke() -> None:
    config = LocalProvider(
        kind="local",
        base_url=os.environ["HYPERWIKI_SMOKE_BASE_URL"],
        model=os.environ["HYPERWIKI_SMOKE_MODEL"],
    )
    result = OpenAICompatibleGenerator(config).generate(
        GenerationRequest(
            task="extraction", system="Reply with one word.", prompt="Say ok.", max_output_tokens=8
        )
    )
    assert result.text.strip()
```

The smoke test targets any OpenAI-compatible endpoint (for example a local Ollama server at `http://localhost:11434/v1`); it is skipped in CI.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --locked pytest tests/test_openai_compat.py -v`
Expected: FAIL with `ModuleNotFoundError`; the live test reports as skipped.

- [ ] **Step 4: Implement `hyperwiki.providers.openai_compat`** per the Interfaces block.

- [ ] **Step 5: Run tests and all checks**

Expected: all pass; live test skipped.

- [ ] **Step 6: Write the provider ADR**

Record in `docs/decisions/000N-provider-contract.md`: the three kinds and when each runs AI work; secrets only through environment variables; the mandatory spending cap and conservative token estimate; the subscription queue contract (agent drafts validated like backend drafts); and the vendor-terms check. For the terms check, read the current Anthropic documentation and terms for Claude Code headless/scheduled use with a subscription, and OpenAI's for `codex exec` with a ChatGPT plan; record findings with links and the access date. Where terms are unclear, mark scheduled agent runs as unresolved and keep "process the queue when the user opens their agent" as the supported path. Also record new runtime dependencies and licenses (pydantic, httpx and their transitive packages, from `uv tree`) for the M4 audit.

Run: `uv run --locked python scripts/check_docs.py`
Expected: passes.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/hyperwiki/providers/openai_compat.py tests/test_openai_compat.py docs/decisions
git commit -m "feat: add OpenAI-compatible generator and provider contract ADR (HW-009)"
```

---

### Task 11: CI on macOS, Windows, and Linux; close out M0 build tasks

**Files:**
- Modify: `.github/workflows/ci.yml`, `docs/plans/BACKLOG.md`, `DEVELOPMENT_PLAN.md`, `README.md`

**Interfaces:**
- Consumes: the whole test suite.
- Produces: a CI job matrix `os: [ubuntu-latest, macos-latest, windows-latest]` with `fail-fast: false`; job name `Provider-free checks (${{ matrix.os }})`; steps unchanged.

- [ ] **Step 1: Add the matrix to `.github/workflows/ci.yml`** (`runs-on: ${{ matrix.os }}`).

- [ ] **Step 2: Push the branch and check CI once**

Run: `git push -u origin m0/contracts`, wait a few minutes, then check the run once:
`curl -s "https://api.github.com/repos/DefaultAaron/hyper-wiki/actions/runs?branch=m0/contracts&per_page=1"` (or the Actions page).
Expected: `conclusion: success` on all three runners. Windows failures most likely involve fixture hashes (Review Focus 1), path handling (2), console encoding (3), or open SQLite files during cleanup; fix and push again.

- [ ] **Step 3: Update status documents**

Mark HW-002, HW-003, HW-004, HW-005, HW-009, and HW-010 done with the date in `docs/plans/BACKLOG.md`; update M0 status in `DEVELOPMENT_PLAN.md` section 6 and the README "Current status" line. State that HW-006–HW-008 remain open.

Run: `uv run --locked python scripts/check_docs.py`
Expected: passes.

- [ ] **Step 4: Commit and push**

```bash
git add .github/workflows/ci.yml docs/plans/BACKLOG.md DEVELOPMENT_PLAN.md README.md
git commit -m "ci: run checks on macOS, Windows, and Linux; close M0 build tasks (HW-010)"
git push
```

Expected: CI green on all three runners for the final commit; then merge `m0/contracts` into `main` when the user approves.

# Test fixtures

All fixture content is synthetic and was written for this project. It contains no third-party text, real library data, or copyrighted papers.

## `libraries/bilingual`

A Zotero-shaped snapshot library with English, Chinese, and mixed-language items:

- `manifest.json`: the library manifest (schema version 1), covering collections with a parent/child hierarchy, items, a duplicate item, an item without attachments, a trashed item, imported and linked-URL attachments, a missing PDF, standalone and child notes, and annotations.
- `storage/`: attachment files, one folder per attachment key as in Zotero's storage. `AT2MIS22/missing.pdf` is deliberately absent. In text attachments, a form feed (U+000C) separates pages.
- `checksums.json`: SHA-256 of every file under `storage/`, keyed by POSIX path relative to `storage/`.

Storage files are marked `binary` in `.gitattributes` so Git never converts line endings and the checksums hold on every operating system. After changing a storage file, regenerate `checksums.json`.

Tests mutate a temporary copy (the `bilingual_snapshot` fixture) with the helpers in `tests/snapshot_mutations.py`; never modify these files from a test.

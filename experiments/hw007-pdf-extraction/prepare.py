"""List the PDFs of a Zotero library for the HW-007 extractor comparison.

Reads the library with Hyper-Wiki's Zotero copy reader (Zotero's own database is never
opened) and writes ``pdfs.json`` to a private directory outside the repository. Each PDF gets
an anonymous ID; the file holds paths, titles, and abstracts, so it must stay private.

A deterministic sample is marked for the slower extractors: up to ``--per-type`` PDFs of each
item type whose Zotero abstract has at least 40 words, plus every PDF whose title contains
Han characters (the Chinese samples).

    uv run --locked python experiments/hw007-pdf-extraction/prepare.py \
        --zotero-dir ~/Zotero --private /path/outside/the/repo
"""

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from hyperwiki.adapters.zotero_db import read_zotero_library

REPO_ROOT = Path(__file__).resolve().parents[2]
HAN = re.compile("[㐀-䶿一-鿿豈-﫿]")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zotero-dir", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--per-type", type=int, default=15)
    args = parser.parse_args()
    private: Path = args.private.expanduser().resolve()
    if private.is_relative_to(REPO_ROOT):
        raise SystemExit("the private directory must be outside the repository")
    private.mkdir(parents=True, exist_ok=True)

    loaded = read_zotero_library(args.zotero_dir.expanduser())
    items = {item.key: item for item in loaded.manifest.items}
    records: list[dict[str, Any]] = []
    for attachment in loaded.manifest.attachments:
        file = loaded.files[attachment.key]
        if attachment.content_type != "application/pdf" or file.resolved_path is None:
            continue
        item = items.get(attachment.parent_key or "")
        abstract = (item.abstract or "") if item else ""
        title = item.title if item else ""
        records.append(
            {
                "order": hashlib.sha256(attachment.key.encode()).hexdigest(),
                "path": str(file.resolved_path),
                "available": file.availability == "available",
                "item_type": item.item_type if item else "standalone",
                "title": title,
                "abstract": abstract,
                "chinese": bool(HAN.search(title)),
                "abstract_words": len(abstract.split()),
            }
        )
    records.sort(key=lambda record: record["order"])
    by_type: dict[str, int] = defaultdict(int)
    for index, record in enumerate(records, start=1):
        record["id"] = f"p{index:03d}"
        eligible = record["available"] and record["abstract_words"] >= 40
        if record["chinese"] or (eligible and by_type[record["item_type"]] < args.per_type):
            record["sample"] = True
            by_type[record["item_type"]] += not record["chinese"]
        else:
            record["sample"] = False
        del record["order"]

    (private / "pdfs.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    sample = [r for r in records if r["sample"]]
    print(
        json.dumps(
            {
                "pdfs": len(records),
                "available": sum(r["available"] for r in records),
                "with_abstract_40_words": sum(r["abstract_words"] >= 40 for r in records),
                "sample": len(sample),
                "sample_by_type": dict(
                    sorted(
                        {
                            t: sum(r["item_type"] == t and not r["chinese"] for r in sample)
                            for t in {r["item_type"] for r in sample}
                        }.items()
                    )
                ),
                "chinese": sum(r["chinese"] for r in records),
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

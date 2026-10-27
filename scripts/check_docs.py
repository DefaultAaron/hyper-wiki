"""Check this repository's current documentation links and required plan files.

Uses only the standard library. External URLs and archived proposal links are
not fetched. This validates documentation consistency, not application behavior.
"""

from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md",
    "PRODUCT_DESIGN.md",
    "DEVELOPMENT_PLAN.md",
    "RESEARCH_REVIEW.md",
    "AGENTS.md",
    "docs/FEASIBILITY.md",
    "docs/decisions/0001-core-architecture.md",
    "docs/plans/EXPERIMENT_PLAN.md",
    "docs/plans/BACKLOG.md",
    "docs/archive/PRODUCT_DESIGN.initial.md",
    "docs/archive/DEVELOPMENT_PLAN.initial.md",
    "docs/archive/RESEARCH_REVIEW.initial.md",
)


def main() -> int:
    errors = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append(f"Missing required file: {name}")
    checked = 0
    for path in sorted(ROOT.rglob("*.md")):
        relative = path.relative_to(ROOT)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if relative.parts[:2] == ("docs", "archive"):
            continue
        checked += 1
        content = path.read_text(encoding="utf-8")
        if not content.endswith("\n"):
            errors.append(f"Missing final newline: {relative}")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
            target = target.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = path.parent / unquote(parsed.path)
            if not resolved.exists():
                errors.append(f"Broken local link in {relative}: {target}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Documentation checks passed: {checked} current Markdown files; required plans present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

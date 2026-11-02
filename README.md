# Hyper-Wiki

A research wiki backed by retrieval-augmented generation, built to help postgraduate students read, connect, and reuse research literature.

The core ingests a Zotero-like library, retains inspectable source evidence, and maintains a linked Markdown wiki. Codex and Claude Code access shared services through MCP, CLI, and workflow skills. A Zotero plugin follows the core; an Obsidian plugin is optional.

## Current status

Product redesigned and roadmap revised 2026-11-02; M0 in progress. The Python package skeleton, locked toolchain, and provider-free CI (HW-001) exist. No application feature, parser benchmark, retrieval benchmark, or agent integration is implemented yet. Hypergraph retrieval is planned as an interchangeable backend and must earn its default position through experiments. No previous example research domain is required.

## Read first

- [Product design](PRODUCT_DESIGN.md)
- [Feasibility assessment](docs/FEASIBILITY.md)
- [Development plan](DEVELOPMENT_PLAN.md)
- [Evaluation plan](docs/plans/EXPERIMENT_PLAN.md)
- [Architecture decisions](docs/decisions/0001-core-architecture.md) and [license policy](docs/decisions/0002-license.md)
- [Execution backlog](docs/plans/BACKLOG.md) and [M0 implementation plan](docs/plans/2026-11-02-m0-contracts-implementation.md)
- [Earlier literature review](RESEARCH_REVIEW.md)
- [Landscape survey of existing tools](docs/LANDSCAPE.md)

The current product and development/experiment plans supersede archived proposals. The literature review is background, not a verified dependency list or evidence that this implementation works.

## Repository setup

Git history is tracked on `main` and pushed to [DefaultAaron/hyper-wiki](https://github.com/DefaultAaron/hyper-wiki) on GitHub. Use the SSH remote:

```sh
git clone git@github.com:DefaultAaron/hyper-wiki.git
```

Keep private library material and runtime workspaces outside tracked source files.

## Development

Requires [uv](https://docs.astral.sh/uv/) 0.11.14 or later. The project targets Python 3.12 (pinned in `.python-version`); uv downloads it if needed. Install the locked environment and run the same provider-free checks as CI:

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
uv run python scripts/check_docs.py
```

The `hyperwiki` command imports a Zotero-shaped snapshot directory into a workspace database and reports status. Each command prints one JSON document (`{"ok": true, "result": …}` or `{"ok": false, "error": …}`) to stdout. Try it on the synthetic bilingual fixture:

```sh
uv run hyperwiki --workspace /tmp/hw-demo import tests/fixtures/libraries/bilingual
uv run hyperwiki --workspace /tmp/hw-demo status
```

Without `--workspace`, the workspace is `$HYPERWIKI_WORKSPACE`, else `~/.hyperwiki`. Hyper-Wiki only reads the snapshot; linked files are read only from directories passed with `--linked-root`.

## License

Hyper-Wiki is licensed under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE). Third-party integrations follow the license policy in [ADR 0002](docs/decisions/0002-license.md).

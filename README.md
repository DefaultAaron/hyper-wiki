# Hyper-Wiki

A research wiki backed by retrieval-augmented generation, built to help postgraduate students read, connect, and reuse research literature.

The core ingests a Zotero-like library, retains inspectable source evidence, and maintains a linked Markdown wiki. Codex and Claude Code access shared services through MCP, CLI, and workflow skills. A Zotero plugin follows the core; an Obsidian plugin is optional.

## Current status

Planning baseline, 2026-10-26. No application, parser benchmark, retrieval benchmark, or agent integration is implemented yet. Hypergraph retrieval is planned as an interchangeable backend and must earn its default position through experiments. No previous example research domain is required.

## Read first

- [Product scope](PRODUCT_DESIGN.md)
- [Feasibility assessment](docs/FEASIBILITY.md)
- [Development plan](DEVELOPMENT_PLAN.md)
- [Experiment protocol](docs/plans/EXPERIMENT_PLAN.md)
- [Architecture decisions](docs/decisions/0001-core-architecture.md)
- [Execution backlog](docs/plans/BACKLOG.md)
- [Earlier literature review](RESEARCH_REVIEW.md)

The current product and development/experiment plans supersede archived proposals. The literature review is background, not a verified dependency list or evidence that this implementation works.

## Repository setup

The initial environment allowed ordinary project files but denied writing `.git`. Git initialization was attempted and did not succeed. In a terminal with permission to create Git metadata, run:

```sh
git init -b main
git add .
git commit -m "docs: establish Hyper-Wiki implementation and experiment baseline"
```

No remote has been created. No project license has been selected; choose one before public distribution. Keep private library material and runtime workspaces outside tracked source files.

Run the documentation checks using the Python standard library:

```sh
python3 scripts/check_docs.py
```

Python packaging, dependencies, and application commands arrive in milestone M0. There is no install command for an application that does not yet exist.

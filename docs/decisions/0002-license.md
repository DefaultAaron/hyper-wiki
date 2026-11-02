# ADR 0002: Apache-2.0 project license and integration license policy

Date: 2026-11-02. Status: accepted.

## Context

The repository is public, and Hyper-Wiki is expected to integrate open-source RAG backends and LLM-wiki front ends. Which projects will be integrated is not yet decided; integration choices follow later planning and experiments. All current code and documentation have a single author, so the license can still be chosen without contributor consent. Relicensing becomes difficult after outside contributions are accepted.

## Decision

License Hyper-Wiki's own source and documentation under the [Apache License, Version 2.0](../../LICENSE). Record the copyright holder and any bundled third-party attributions in [NOTICE](../../NOTICE). Package metadata declares the SPDX expression `Apache-2.0`.

Reasons:

- A permissive license keeps every candidate integration path open while backends and front ends are undecided. Apache-2.0 code can be combined with MIT, BSD, and Apache-2.0 components, and can be incorporated into GPLv3 or AGPLv3 combined works if a copyleft component is later adopted.
- Apache-2.0 includes an explicit patent grant and a defined NOTICE mechanism, which suit research and ML tooling better than MIT.
- Researchers and organizations can adopt or extend the tool without copyleft obligations on their own code.

## Integration license policy

Before adopting any third-party project, record its license, version, and integration mode in the decision or experiment report that adopts it. Check compatibility for the actual mode:

| Integration mode | Typical compatibility for this Apache-2.0 repository |
|---|---|
| Optional runtime dependency installed separately (pip/uv dependency) | Permissive licenses fine. Copyleft dependencies need review of distribution and linking obligations before becoming default. |
| Separate process or service called over CLI/HTTP/MCP | Usually does not combine licenses; still record terms and any network-use clauses (for example AGPL). |
| Vendored or copied source in this repository | Only compatible permissive licenses (MIT, BSD, ISC, Apache-2.0, similar). Preserve headers and add attribution to NOTICE. |
| Plugin package (Zotero, Obsidian) | May live in its own directory with its own license if its host template or API requires one; document the boundary. |
| Model weights, datasets, corpora | Licensed separately from code; check use restrictions (for example non-commercial or OpenRAIL terms) and never commit restricted artifacts. |

Revisit this decision before vendoring or bundling any GPL/AGPL/LGPL code, before adopting a dependency whose license is not OSI-approved, and before accepting outside contributions without a contribution sign-off policy.

## Consequences

Users may build closed products on Hyper-Wiki. Copyleft projects remain usable as optional or out-of-process integrations but cannot be copied into this repository without revisiting the license. M5's dependency/license audit remains required before a packaged release.

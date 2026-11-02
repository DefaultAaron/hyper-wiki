# ADR 0003: AI provider contract

Date: 2026-11-02. Status: accepted (HW-009). Implemented in `src/hyperwiki/domain/providers.py` and `src/hyperwiki/providers/`.

## Context

The [product design](../../PRODUCT_DESIGN.md) requires AI work (relation extraction, page writing and revision, synthesis) to run through one interface for three kinds of provider: a metered API key, a local model, and the user's existing subscription through their own agent. Non-AI processing must work with no provider configured. Hyper-Wiki must never handle subscription credentials and must always enforce a spending cap. This record fixes the contract that the M2 provider runtime builds on, and records what vendor terms say about running subscription agents on a schedule.

## Decision

### Provider kinds

| Kind | Configuration | When AI work runs | Cost |
|---|---|---|---|
| `api` | `base_url`, `model`, `api_key_env`, `daily_spend_cap_usd` (required, > 0), input and output prices per million tokens | Automatically, during automatic or scheduled updates, until the cap is reached | Metered, capped |
| `local` | `base_url`, `model` of an OpenAI-compatible server (Ollama, LM Studio, or similar) | Automatically, during automatic or scheduled updates | Zero to Hyper-Wiki |
| `subscription` | `agent`: `claude-code`, `codex`, or `any` | When the user's own agent processes the queue | Drawn from the user's own plan by their own agent |

Settings have a `default` provider and optional per-task `overrides` for `extraction`, `page_writing`, and `synthesis`, so a user can, for example, extract with a local model and write pages through their subscription. Settings are a versioned document (`ProviderSettings`, schema version 1). `api` and `local` providers use one OpenAI-compatible client (`POST {base_url}/chat/completions`). A provider-specific client is added only when a needed feature is missing from the compatible endpoint.

Non-AI stages (inventory, extraction, evidence, keyword index, local embeddings, invalidation) never call a provider.

### Secrets

- Configuration names an environment variable (`api_key_env`) and never contains a key. A provider entry with a literal `api_key` field is rejected with `invalid_document`, and the error message and details name only the provider's location.
- A missing variable fails with `provider_not_configured`, naming the variable and never a value.
- The key is held in memory only. It is sent only in the `Authorization` header to the configured endpoint, and it is redacted from any response excerpt included in error details.
- For subscriptions, Hyper-Wiki performs no sign-in, reads no agent credential files (such as `~/.claude` credentials or `~/.codex/auth.json`), and never starts an agent with the user's login on its own initiative.

### Spending cap

- An `api` provider without `daily_spend_cap_usd` is rejected with `spending_cap_required`, and the client refuses to build an `api` generator without a `SpendingGuard`.
- Before each call, the guard checks a worst-case estimate: input tokens estimated as one token per four ASCII characters plus one per other character (which overestimates Chinese text), and output at the full `max_output_tokens`. A call that would take spending past the cap fails with `spending_cap_reached` and is never sent.
- After each call, the actual cost from the reported `usage` (or the estimates when `usage` is absent) is recorded.
- Daily reset, persistence across processes, and visible spending status arrive with the M2 runtime.

### Subscription queue

Hyper-Wiki records AI work as `AITask`s (`task_id`, `kind` of `write_page`, `revise_page`, or `extract_relations`, `target_id`, `input_evidence_ids`, `instructions`, `created_at`, `status`). The user's agent lists pending tasks and returns `DraftSubmission`s (`task_id`, `content_markdown`, `cited_evidence_ids`) through MCP tools that arrive in M2 with a workflow skill.

- Submitting for an unknown or already-completed task fails with `not_found`.
- Citing evidence that was not supplied with the task fails with `invalid_document` listing the unknown IDs.
- In M2, agent drafts pass the same citation validation and lint as backend drafts, are recorded with a `subscription` generator fingerprint, and are never treated as evidence.

While tasks wait, the affected pages show as pending AI work; nothing is generated in the background.

### Errors

Non-2xx responses, timeouts, transport failures, and malformed bodies raise `provider_error` with `status_code` (or null) and the endpoint URL. None of these errors includes the API key.

## Vendor terms for scheduled agent runs

Checked on 2026-11-02 against the vendors' own documentation; see the sources below.

**Anthropic (Claude Code).**

- Free, Pro, Max, and Ultra use falls under the Consumer Terms, and "advertised usage limits for Pro, Max, and Ultra plans assume ordinary, individual usage of Claude Code and the Agent SDK."
- OAuth sign-in "is intended exclusively for purchasers of" subscription plans and "is designed to support ordinary use of Claude Code and other native Anthropic applications."
- Developers building products, including with the Agent SDK, "should use API key authentication." Anthropic "does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, Max, or Ultra plan credentials on behalf of their users." Developers "may not collect, store, or intermediate Claude.ai credentials or session tokens."
- This does not prevent "an end user from signing in to the unmodified Claude Code binary with their own Claude subscription."
- `claude --bare -p` never reads OAuth credentials and needs an API key; non-bare `claude -p` can use the subscription login.
- The Help Center says Agent SDK and `claude -p` use with a plan sign-in draws from subscription limits. The separate Agent SDK credit announced for June 15, 2026 was paused. The 2026-10-07 update says the monthly API credits included with Max, Ultra, and Team apply only when these tools run with an API key from the linked Console organization.

**OpenAI (Codex).** Codex supports sign-in with ChatGPT (plan credits) or an API key (API pricing). The documentation says to "use API key authentication for programmatic Codex CLI workflows, such as CI/CD jobs" and that "API keys are still the recommended default for automation." Automation access tokens exist only as a ChatGPT Enterprise admin feature "intended for trusted scripts, schedulers, and private CI runners." `~/.codex/auth.json` is to be treated like a password.

**Conclusions.**

- **Supported:** process the queue when the user opens their agent. The user runs their own unmodified Claude Code or Codex, signed in with their own plan; the agent calls Hyper-Wiki's MCP tools, and Hyper-Wiki touches no credentials. This is ordinary, individual use of the vendor's own application.
- **Unresolved:** Hyper-Wiki starting `claude -p` or `codex exec` unattended (for example from the scheduled check) with the user's plan login. Neither vendor explicitly prohibits a user scheduling their own agent. However, both frame plans around ordinary individual use and recommend API keys for automation, and Anthropic forbids third-party tools routing requests through plan credentials on users' behalf. A tool that ships subscription-backed scheduled runs risks falling on the wrong side of that line.
- **Decision until the terms are explicit:** Hyper-Wiki does not launch agents. Scheduled and automatic updates do AI work only with `api` or `local` providers. With `subscription`, they do the non-AI work, queue the AI tasks, and report them as pending. Revisit this record when either vendor publishes guidance for scheduled personal use.

## Runtime dependencies added in M0

Recorded for the M4 license audit. Versions are from `uv.lock` on 2026-11-02; licenses are from installed package metadata. All are installed, unmodified dependencies, which [ADR 0002](0002-license.md) allows for permissive and weak-copyleft licenses.

| Package | Version | License | Pulled in by |
|---|---|---|---|
| pydantic | 2.14.2 | MIT | Hyper-Wiki |
| pydantic-core | 2.50.2 | MIT | pydantic |
| annotated-types | 0.8.0 | MIT | pydantic |
| typing-inspection | 0.4.4 | MIT | pydantic |
| typing-extensions | 4.16.0 | PSF-2.0 | pydantic, anyio |
| httpx | 0.28.1 | BSD-3-Clause | Hyper-Wiki |
| httpcore | 1.0.9 | BSD-3-Clause | httpx |
| h11 | 0.16.0 | MIT | httpcore |
| anyio | 4.16.0 | MIT | httpx |
| idna | 3.20 | BSD-3-Clause | httpx, anyio |
| certifi | 2026.10.23 | MPL-2.0 | httpx, httpcore |

certifi's MPL-2.0 is file-level copyleft. Using it unmodified imposes nothing on Hyper-Wiki's code; if it were ever vendored or modified, its files would stay under MPL-2.0.

## Consequences and open items

- M2 adds the provider runtime: persistent spending with daily reset, a persistent task queue, MCP tools for listing pending tasks and submitting drafts, and the workflow skill for Claude Code and Codex.
- The opt-in live smoke test (`HYPERWIKI_SMOKE_BASE_URL`, `HYPERWIKI_SMOKE_MODEL`) was not run on 2026-11-02 because no local OpenAI-compatible server was available, so the client has been verified only against mocked HTTP responses.
- Token estimates are deliberately conservative; M2 should compare them with reported usage on English and Chinese prompts before tuning.

## Sources

Accessed 2026-11-02.

- Anthropic, [Claude Code: Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance) (authentication and credential use; usage policy).
- Anthropic, [Claude Code: Run Claude Code programmatically](https://code.claude.com/docs/en/headless) (`claude -p`, bare mode, unattended runs).
- Anthropic Help Center, [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) (paused Agent SDK credit; 2026-10-07 update).
- Anthropic, [Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms) and [Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms).
- OpenAI, [Codex authentication](https://learn.chatgpt.com/docs/auth) (ChatGPT sign-in vs API key; automation recommendations; access tokens).

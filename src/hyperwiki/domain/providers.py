"""AI provider configuration and the subscription-queue task contract.

Three provider kinds run AI work: ``api`` (a metered API key, always under a spending cap),
``local`` (an OpenAI-compatible server on the user's machine), and ``subscription`` (tasks are
queued for the user's own agent, such as Claude Code or Codex, to draft). Hyper-Wiki never
stores API keys or handles subscription credentials.
"""

from collections.abc import Mapping
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl

from hyperwiki.domain.common import Contract, VersionedDocument, parse_document
from hyperwiki.domain.evidence import EvidenceId
from hyperwiki.errors import ErrorCode, HyperWikiError

TaskName = Literal["extraction", "page_writing", "synthesis"]

NonEmptyStr = Annotated[str, Field(min_length=1)]


class ApiProvider(Contract):
    kind: Literal["api"]
    base_url: HttpUrl
    model: NonEmptyStr
    api_key_env: Annotated[str, Field(pattern=r"^[A-Z_][A-Z0-9_]*$")]
    daily_spend_cap_usd: Annotated[float, Field(gt=0)]
    input_usd_per_million_tokens: Annotated[float, Field(ge=0)]
    output_usd_per_million_tokens: Annotated[float, Field(ge=0)]


class LocalProvider(Contract):
    kind: Literal["local"]
    base_url: HttpUrl
    model: NonEmptyStr


class SubscriptionProvider(Contract):
    kind: Literal["subscription"]
    agent: Literal["claude-code", "codex", "any"] = "any"


AnyProvider = ApiProvider | LocalProvider | SubscriptionProvider
ProviderConfig = Annotated[AnyProvider, Field(discriminator="kind")]


class ProviderSettings(VersionedDocument):
    default: ProviderConfig
    overrides: dict[TaskName, ProviderConfig] = Field(default_factory=dict)

    def for_task(self, task: TaskName) -> AnyProvider:
        return self.overrides.get(task, self.default)


def _provider_entries(data: Mapping[object, object]) -> list[tuple[str, Mapping[object, object]]]:
    entries: list[tuple[str, Mapping[object, object]]] = []
    default = data.get("default")
    if isinstance(default, Mapping):
        entries.append(("default", default))
    overrides = data.get("overrides")
    if isinstance(overrides, Mapping):
        entries.extend(
            (f"overrides.{task}", entry)
            for task, entry in overrides.items()
            if isinstance(entry, Mapping)
        )
    return entries


def parse_provider_settings(data: object) -> ProviderSettings:
    """Parse provider settings, refusing literal API keys and API providers without a cap."""
    if isinstance(data, Mapping):
        for where, entry in _provider_entries(data):
            if "api_key" in entry:
                raise HyperWikiError(
                    ErrorCode.INVALID_DOCUMENT,
                    "store the key in an environment variable and reference it with api_key_env",
                    {"provider": where},
                )
            if entry.get("kind") == "api" and "daily_spend_cap_usd" not in entry:
                raise HyperWikiError(
                    ErrorCode.SPENDING_CAP_REQUIRED,
                    "an api provider requires daily_spend_cap_usd",
                    {"provider": where},
                )
    return parse_document(ProviderSettings, data, include_input=False)


AITaskKind = Literal["write_page", "revise_page", "extract_relations"]
AITaskStatus = Literal["pending", "done"]


class AITask(Contract):
    """Work queued for the user's agent when the subscription provider is selected."""

    task_id: UUID
    kind: AITaskKind
    target_id: str
    input_evidence_ids: list[EvidenceId]
    instructions: str
    created_at: AwareDatetime
    status: AITaskStatus = "pending"


class DraftSubmission(Contract):
    task_id: UUID
    content_markdown: str
    cited_evidence_ids: list[EvidenceId]

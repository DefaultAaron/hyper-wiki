import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hyperwiki.domain.providers import AITask, DraftSubmission, parse_provider_settings
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.providers.base import GenerationRequest
from hyperwiki.providers.fake import FakeGenerator
from hyperwiki.providers.queue import InMemoryTaskQueue
from hyperwiki.providers.spending import SpendingGuard, estimate_tokens

API = {
    "kind": "api",
    "base_url": "https://api.example.com/v1",
    "model": "m",
    "api_key_env": "HW_TEST_KEY",
    "daily_spend_cap_usd": 1.0,
    "input_usd_per_million_tokens": 1.0,
    "output_usd_per_million_tokens": 2.0,
}
EVIDENCE = "ev_" + "1" * 32
SECRET = "sk-live-SECRET123"


def make_task() -> AITask:
    return AITask(
        task_id=uuid4(),
        kind="write_page",
        target_id="page-1",
        input_evidence_ids=[EVIDENCE],
        instructions="Write the paper page.",
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
    )


def test_api_provider_requires_spending_cap() -> None:
    api = {k: v for k, v in API.items() if k != "daily_spend_cap_usd"}
    with pytest.raises(HyperWikiError) as caught:
        parse_provider_settings({"schema_version": 1, "default": api})
    assert caught.value.code is ErrorCode.SPENDING_CAP_REQUIRED


def test_api_override_requires_spending_cap() -> None:
    api = {k: v for k, v in API.items() if k != "daily_spend_cap_usd"}
    with pytest.raises(HyperWikiError) as caught:
        parse_provider_settings(
            {
                "schema_version": 1,
                "default": {"kind": "subscription"},
                "overrides": {"synthesis": api},
            }
        )
    assert caught.value.code is ErrorCode.SPENDING_CAP_REQUIRED


def test_literal_api_key_is_rejected() -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_provider_settings({"schema_version": 1, "default": API | {"api_key": "sk-secret"}})
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT
    assert "sk-secret" not in str(caught.value)
    assert "sk-secret" not in repr(caught.value.details)


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
    assert set(caught.value.details) == {"cap_usd", "spent_usd", "estimated_usd"}


def test_fake_generator_is_deterministic() -> None:
    request = GenerationRequest(task="page_writing", system="s", prompt="p", max_output_tokens=10)
    fake = FakeGenerator()
    assert fake.generate(request) == fake.generate(request)
    assert len(fake.calls) == 2
    assert fake.generate(request).cost_usd == 0.0
    scripted = FakeGenerator(responses={"p": "注意力"})
    assert scripted.generate(request).text == "注意力"


def test_queue_round_trip() -> None:
    queue = InMemoryTaskQueue()
    task = make_task()
    queue.enqueue(task)
    assert queue.pending() == [task]
    done = queue.submit(
        DraftSubmission(
            task_id=task.task_id, content_markdown="# Page", cited_evidence_ids=[EVIDENCE]
        )
    )
    assert done.status == "done"
    assert queue.pending() == []


def test_queue_rejects_unknown_evidence() -> None:
    queue = InMemoryTaskQueue()
    task = make_task()
    queue.enqueue(task)
    unknown = "ev_" + "2" * 32
    with pytest.raises(HyperWikiError) as caught:
        queue.submit(
            DraftSubmission(
                task_id=task.task_id, content_markdown="x", cited_evidence_ids=[unknown]
            )
        )
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT
    assert caught.value.details["unknown_evidence_ids"] == [unknown]
    assert queue.pending() == [task]


def test_queue_rejects_unknown_task() -> None:
    queue = InMemoryTaskQueue()
    with pytest.raises(HyperWikiError) as caught:
        queue.submit(DraftSubmission(task_id=uuid4(), content_markdown="x", cited_evidence_ids=[]))
    assert caught.value.code is ErrorCode.NOT_FOUND


def test_queue_rejects_resubmission() -> None:
    queue = InMemoryTaskQueue()
    task = make_task()
    queue.enqueue(task)
    draft = DraftSubmission(task_id=task.task_id, content_markdown="x", cited_evidence_ids=[])
    queue.submit(draft)
    with pytest.raises(HyperWikiError) as caught:
        queue.submit(draft)
    assert caught.value.code is ErrorCode.NOT_FOUND


@pytest.mark.parametrize(
    "settings",
    [
        {"schema_version": 1, "default": API | {"api_key_env": SECRET}},
        {"schema_version": 1, "default": API | {"apiKey": SECRET}},
        {"schema_version": 1, "default": API, "api_key": SECRET},
    ],
)
def test_provider_errors_never_echo_values(settings: dict[str, object]) -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_provider_settings(settings)
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT
    assert SECRET not in str(caught.value)
    assert SECRET not in json.dumps(caught.value.details)

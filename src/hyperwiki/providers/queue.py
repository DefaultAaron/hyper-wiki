"""Subscription queue: AI tasks drafted by the user's own agent instead of a metered API."""

from typing import Protocol
from uuid import UUID

from hyperwiki.domain.providers import AITask, DraftSubmission
from hyperwiki.errors import ErrorCode, HyperWikiError


class TaskQueue(Protocol):
    def enqueue(self, task: AITask) -> None: ...

    def pending(self) -> list[AITask]: ...

    def submit(self, draft: DraftSubmission) -> AITask: ...


class InMemoryTaskQueue:
    """Reference queue; the persistent queue arrives with the M2 provider runtime."""

    def __init__(self) -> None:
        self._tasks: dict[UUID, AITask] = {}

    def enqueue(self, task: AITask) -> None:
        self._tasks[task.task_id] = task

    def pending(self) -> list[AITask]:
        tasks = [task for task in self._tasks.values() if task.status == "pending"]
        return sorted(tasks, key=lambda task: (task.created_at, str(task.task_id)))

    def submit(self, draft: DraftSubmission) -> AITask:
        task = self._tasks.get(draft.task_id)
        if task is None or task.status != "pending":
            raise HyperWikiError(
                ErrorCode.NOT_FOUND,
                "no pending task with this ID",
                {"task_id": str(draft.task_id)},
            )
        allowed = set(task.input_evidence_ids)
        unknown = list(dict.fromkeys(e for e in draft.cited_evidence_ids if e not in allowed))
        if unknown:
            raise HyperWikiError(
                ErrorCode.INVALID_DOCUMENT,
                "the draft cites evidence that was not provided with the task",
                {"unknown_evidence_ids": unknown},
            )
        done = task.model_copy(update={"status": "done"})
        self._tasks[task.task_id] = done
        return done

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from uuid import UUID

from fairy_core.assistant.candidates import ToolCandidate
from fairy_core.assistant.models import Message
from fairy_core.commanding.registry import ToolConcurrency, ToolDefinition
from fairy_core.providers import CancellationToken, ModelImage


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    candidate: ToolCandidate
    waiting: bool
    message: Message | None
    error_code: str | None
    images: tuple[ModelImage, ...]


class AssistantParallelToolsMixin:
    def _execute_candidate_batch(
        self,
        *,
        turn_id: UUID,
        candidates: tuple[ToolCandidate, ...],
        model_round: int,
        tool_count: int,
        max_parallel: int,
        cancellation: CancellationToken,
        offered_definitions: dict[str, ToolDefinition],
    ) -> tuple[tuple[ToolExecutionOutcome, ...], int]:
        outcomes: list[ToolExecutionOutcome] = []
        for batch in _candidate_batches(candidates, offered_definitions, max_parallel=max_parallel):
            self._raise_if_workflow_paused(turn_id)
            start_sequence = tool_count + 1
            if len(batch) == 1:
                results = (
                    self._execute_candidate(
                        turn_id=turn_id,
                        candidate=batch[0],
                        model_round=model_round,
                        sequence=start_sequence,
                        cancellation=cancellation,
                        offered_definitions=offered_definitions,
                    ),
                )
            else:
                self._reserve_parallel_tool_budget(turn_id, len(batch))
                with ThreadPoolExecutor(
                    max_workers=len(batch),
                    thread_name_prefix="fairy-tool-read",
                ) as executor:
                    futures = tuple(
                        executor.submit(
                            self._execute_candidate,
                            turn_id=turn_id,
                            candidate=candidate,
                            model_round=model_round,
                            sequence=start_sequence + index,
                            cancellation=cancellation,
                            offered_definitions=offered_definitions,
                            budget_reserved=True,
                        )
                        for index, candidate in enumerate(batch)
                    )
                    results = tuple(future.result() for future in futures)
            tool_count += len(batch)
            outcomes.extend(
                ToolExecutionOutcome(candidate, waiting, message, error_code, images)
                for candidate, (waiting, message, error_code, images) in zip(
                    batch,
                    results,
                    strict=True,
                )
            )
            if any(result[0] for result in results):
                break
        return tuple(outcomes), tool_count


def _candidate_batches(
    candidates: tuple[ToolCandidate, ...],
    definitions: dict[str, ToolDefinition],
    *,
    max_parallel: int,
) -> tuple[tuple[ToolCandidate, ...], ...]:
    if max_parallel < 1:
        raise ValueError("parallel tool limit must be positive")
    batches: list[tuple[ToolCandidate, ...]] = []
    pending: list[ToolCandidate] = []
    resource_keys: set[str] = set()

    def flush() -> None:
        nonlocal pending, resource_keys
        if pending:
            batches.append(tuple(pending))
            pending = []
            resource_keys = set()

    for candidate in candidates:
        definition = definitions.get(candidate.name or "")
        if definition is None or definition.concurrency_policy is ToolConcurrency.SERIAL:
            flush()
            batches.append((candidate,))
            continue
        keys = set(definition.concurrency_resource_keys)
        if len(pending) >= max_parallel or resource_keys.intersection(keys):
            flush()
        pending.append(candidate)
        resource_keys.update(keys)
    flush()
    return tuple(batches)


__all__ = ["AssistantParallelToolsMixin", "ToolExecutionOutcome"]

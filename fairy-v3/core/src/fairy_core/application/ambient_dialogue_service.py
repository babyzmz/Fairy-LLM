from __future__ import annotations

from dataclasses import asdict, replace
from threading import Lock, Thread

from fairy_core.contracts.persona import (
    AmbientDialogueDecisionModel,
    AmbientDialogueEvaluateInput,
)
from fairy_core.persona import (
    AmbientContextSnapshot,
    AmbientDialogueDecision,
    AmbientDialogueGenerator,
    AmbientDialoguePreferences,
    AmbientDialogueState,
    FairyDialogueDirector,
    GeneratedDialogueCandidate,
)


class _PendingGeneration:
    def __init__(
        self,
        *,
        deferred: AmbientDialogueDecision,
        fallback: AmbientDialogueDecision,
        context: AmbientContextSnapshot,
    ) -> None:
        self.deferred = deferred
        self.fallback = fallback
        self.context = context
        self.completed = False
        self.candidate: GeneratedDialogueCandidate | None = None


class AmbientDialogueService:
    def __init__(
        self,
        director: FairyDialogueDirector,
        generator: AmbientDialogueGenerator | None = None,
    ) -> None:
        self._director = director
        self._generator = generator
        self._generation_lock = Lock()
        self._pending_generation: _PendingGeneration | None = None

    def evaluate(self, request: AmbientDialogueEvaluateInput) -> AmbientDialogueDecisionModel:
        context = AmbientContextSnapshot(**request.context.model_dump())
        preferences = AmbientDialoguePreferences(**request.preferences.model_dump())
        state_data = request.state.model_dump()
        state_data["generated_digests"] = tuple(state_data["generated_digests"])
        state = AmbientDialogueState(**state_data)

        pending = self._consume_pending(context, preferences, state)
        if pending is not None:
            return AmbientDialogueDecisionModel.model_validate(asdict(pending))

        decision = self._director.evaluate(context, preferences, state)
        if decision.generation_request is not None and self._generator is not None:
            decision = self._director.record_generation_attempt(decision)
            deferred = self._director.defer_generation(
                decision,
                previous_state=state,
                observed_at=context.observed_at,
            )
            self._start_generation(
                _PendingGeneration(
                    deferred=deferred,
                    fallback=decision,
                    context=context,
                )
            )
            decision = deferred
        elif decision.generation_request is not None:
            decision = replace(decision, generation_request=None)
        return AmbientDialogueDecisionModel.model_validate(asdict(decision))

    def _consume_pending(
        self,
        context: AmbientContextSnapshot,
        preferences: AmbientDialoguePreferences,
        state: AmbientDialogueState,
    ) -> AmbientDialogueDecision | None:
        with self._generation_lock:
            pending = self._pending_generation
            if pending is None:
                return None
            if state != pending.deferred.next_state:
                self._pending_generation = None
                return None
            if not pending.completed:
                return pending.deferred

            guard = self._director.evaluate(context, preferences, state)
            fallback_projection = pending.fallback.projection
            if (
                guard.projection is None
                or fallback_projection is None
                or guard.projection.trigger is not fallback_projection.trigger
                or guard.projection.locale != pending.context.locale
                or guard.projection.persona_digest != fallback_projection.persona_digest
            ):
                self._pending_generation = None
                return guard

            self._pending_generation = None
            candidate = pending.candidate
            if candidate is None or not set(candidate.required_facts).issubset(
                context.true_facts()
            ):
                return replace(guard, generation_request=None)
            return self._director.apply_generated(
                guard,
                candidate,
                context=context,
                preferences=preferences,
            )

    def _start_generation(self, pending: _PendingGeneration) -> None:
        with self._generation_lock:
            if self._pending_generation is not None:
                return
            self._pending_generation = pending

        def run() -> None:
            candidate: GeneratedDialogueCandidate | None = None
            try:
                assert self._generator is not None
                assert pending.fallback.generation_request is not None
                generated = self._generator.generate(
                    pending.fallback.generation_request,
                    pending.context,
                )
                if generated is not None:
                    candidate = generated.candidate
            except Exception:
                # Provider failures must not take down or occupy the local Core RPC loop.
                pass
            with self._generation_lock:
                if self._pending_generation is pending:
                    pending.candidate = candidate
                    pending.completed = True

        Thread(
            target=run,
            name="fairy-ambient-dialogue-generation",
            daemon=True,
        ).start()


__all__ = ["AmbientDialogueService"]

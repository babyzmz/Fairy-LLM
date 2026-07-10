from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.commanding import CommandRun, CommandStatus, EventEnvelope, EventVisibility
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.models import (
    MemoryClaimGetInput,
    MemoryClaimPromoteInput,
    MemoryClaimQuery,
    MemoryClaimResolveInput,
    MemoryClaimSupersedeInput,
    MemoryForgetInput,
    MemoryObservationQuery,
    MemoryObserveInput,
)
from fairy_core.domain.errors import (
    InvalidTransitionError,
    MemoryConflictError,
    MemoryForgottenError,
    MemoryInjectionBlockedError,
    MemoryScopeViolationError,
    MemorySecretBlockedError,
)
from fairy_core.domain.models import ScopeContract, Task
from fairy_core.memory.models import (
    MemoryAuthority,
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
    MemorySensitivity,
    MemorySourceType,
    MemoryTargetKind,
    MemoryTombstone,
    ObservationStatus,
)
from fairy_core.memory.policy import MemoryPolicy, MemoryPolicyDecision
from fairy_core.memory.projection import LexicalProjectionRefresher
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.storage.ports import StateStore

ScopeResolver = Callable[[StateStore, Task], ScopeContract]
_USER_ACTOR = "user:core-client"

_READ_SCOPE_ALIASES: dict[MemoryNamespace, frozenset[str]] = {
    MemoryNamespace.PROJECT_CANONICAL: frozenset({"project_canonical"}),
    MemoryNamespace.CONVERSATION_DRAFT: frozenset(
        {"conversation_draft", "current_conversation", "current_conversation_draft"}
    ),
    MemoryNamespace.USER_PROFILE: frozenset({"user_profile", "personal"}),
    MemoryNamespace.DEVICE_LOCAL: frozenset({"device_local"}),
    MemoryNamespace.TASK_EPISODE: frozenset({"task_episode", "failure_lesson"}),
}


@dataclass(frozen=True, slots=True)
class MemoryClaimContext:
    claim: MemoryClaim
    current_revision: MemoryClaimRevision


@dataclass(frozen=True, slots=True)
class _PreparedCommand:
    run: CommandRun
    execute: bool


class MemoryApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        registry: ToolRegistry,
        command_policy: PolicyEngine,
        memory_policy: MemoryPolicy,
        scope_resolver: ScopeResolver,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._command_policy = command_policy
        self._memory_policy = memory_policy
        self._scope_resolver = scope_resolver

    def observe(self, request: MemoryObserveInput) -> MemoryObservation:
        self._require_safe_content(request.content)
        with self._unit_of_work_factory() as unit_of_work:
            task, base_scope = self._task_scope(unit_of_work, request.task_id)
            scope = self._scope_with_write(base_scope, MemoryNamespace.CONVERSATION_DRAFT)
            command = self._prepare_command(
                unit_of_work,
                tool_name="memory.observe",
                scope=scope,
                payload={
                    "task_id": str(task.id),
                    "content": request.content,
                },
                idempotency_key=request.idempotency_key,
                approval_confirmed=False,
            )
            source_event = self._command_source_event(unit_of_work, command.run.id)
            observation = MemoryObservation.create(
                scope=scope,
                source_event_id=source_event.id,
                source_cursor=source_event.cursor,
                source_type=MemorySourceType.EXPLICIT_USER_ACTION,
                content=request.content,
                proposed_namespace=MemoryNamespace.CONVERSATION_DRAFT,
                authority=MemoryAuthority.EXPLICIT_USER,
                confidence=1.0,
                sensitivity=MemorySensitivity.PRIVATE,
                actor=_USER_ACTOR,
            )
            decision = self._memory_policy.evaluate_promotion(
                observation,
                MemoryNamespace.CONVERSATION_DRAFT,
                scope,
            )
            self._require_policy_allowed(decision)
            observation = observation.transition_to(
                ObservationStatus.ACCEPTED,
                scan_result=decision.scan_result,
            )
            persisted = unit_of_work.memory.append_observation(
                observation,
                request_fingerprint=self._stage_fingerprint(command.run.id, "observation"),
            )
            if command.execute:
                self._finish(
                    unit_of_work,
                    command.run,
                    event_type="memory.observation.accepted",
                    message="Memory observation accepted",
                    payload={
                        "observation_id": str(persisted.id),
                        "namespace": persisted.proposed_namespace.value,
                        "status": persisted.status.value,
                    },
                )
            unit_of_work.commit()
            source_run_id = command.run.id
        self._refresh_projection_after_commit(request.task_id, source_run_id)
        return persisted

    def list_observations(
        self,
        request: MemoryObservationQuery,
    ) -> tuple[MemoryObservation, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            _task, scope = self._task_scope(unit_of_work, request.task_id)
            self._require_read_namespace(scope, request.namespace)
            arguments = self._scope_arguments(scope, request.namespace)
            return tuple(
                unit_of_work.memory.observations_for_scope(
                    namespace=request.namespace,
                    project_id=arguments["project_id"],
                    conversation_id=arguments["conversation_id"],
                    task_id=arguments["task_id"],
                )
            )

    def promote_claim(self, request: MemoryClaimPromoteInput) -> MemoryClaimContext:
        self._require_confirmation(request.user_confirmed)
        self._require_safe_claim_value(request.value, request.normalized_text)
        with self._unit_of_work_factory() as unit_of_work:
            task, base_scope = self._task_scope(unit_of_work, request.task_id)
            if task.project_id is None:
                raise MemoryScopeViolationError("Project Canonical Memory requires a project Task")
            observation = self._require_observation(
                unit_of_work,
                request.observation_id,
            )
            self._require_observation_scope(observation, base_scope)
            target_scope = self._scope_with_write(
                base_scope,
                MemoryNamespace.PROJECT_CANONICAL,
            )
            command = self._prepare_command(
                unit_of_work,
                tool_name="memory.claim.promote",
                scope=target_scope,
                payload=request.model_dump(mode="json"),
                idempotency_key=request.idempotency_key,
                approval_confirmed=True,
            )
            source_event = self._command_source_event(unit_of_work, command.run.id)
            candidate = MemoryClaim.create(
                namespace=MemoryNamespace.PROJECT_CANONICAL,
                project_id=task.project_id,
                subject=request.subject,
                predicate=request.predicate,
            )
            claim = unit_of_work.memory.create_claim(
                candidate,
                request_fingerprint=self._stage_fingerprint(command.run.id, "claim"),
            )
            revision = MemoryClaimRevision.create(
                claim_id=claim.id,
                revision=1,
                value=request.value,
                normalized_text=request.normalized_text,
                source_observation_ids=(observation.id,),
                source_event_ids=self._source_event_ids((observation,), source_event),
                authority=MemoryAuthority.EXPLICIT_USER,
                confidence=observation.confidence,
                valid_from=request.valid_from,
                valid_to=request.valid_to,
                actor=_USER_ACTOR,
            )
            claim = unit_of_work.memory.append_revision(
                claim.id,
                expected_revision=0,
                revision=revision,
                request_fingerprint=self._stage_fingerprint(command.run.id, "revision"),
            )
            context = self._claim_context(unit_of_work, claim)
            if command.execute:
                self._finish(
                    unit_of_work,
                    command.run,
                    event_type="memory.claim.promoted",
                    message="Memory claim promoted",
                    payload={
                        "claim_id": str(claim.id),
                        "namespace": claim.namespace.value,
                        "revision": context.current_revision.revision,
                        "observation_id": str(observation.id),
                    },
                )
            unit_of_work.commit()
            source_run_id = command.run.id
        self._refresh_projection_after_commit(request.task_id, source_run_id)
        return context

    def get_claim(self, request: MemoryClaimGetInput) -> MemoryClaimContext:
        with self._unit_of_work_factory() as unit_of_work:
            _task, scope = self._task_scope(unit_of_work, request.task_id)
            claim = self._require_claim(unit_of_work, request.claim_id)
            self._require_claim_scope(claim, scope)
            return self._claim_context(unit_of_work, claim)

    def list_claims(self, request: MemoryClaimQuery) -> tuple[MemoryClaimContext, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            _task, scope = self._task_scope(unit_of_work, request.task_id)
            self._require_read_namespace(scope, request.namespace)
            arguments = self._scope_arguments(scope, request.namespace)
            claims = unit_of_work.memory.claims_for_scope(
                namespace=request.namespace,
                project_id=arguments["project_id"],
                conversation_id=arguments["conversation_id"],
                task_id=arguments["task_id"],
                device_id=arguments["device_id"],
            )
            return tuple(self._claim_context(unit_of_work, claim) for claim in claims)

    def supersede_claim(
        self,
        request: MemoryClaimSupersedeInput,
    ) -> MemoryClaimContext:
        self._require_confirmation(request.user_confirmed)
        self._require_safe_claim_value(request.value, request.normalized_text)
        with self._unit_of_work_factory() as unit_of_work:
            _task, base_scope = self._task_scope(unit_of_work, request.task_id)
            claim = self._require_claim(unit_of_work, request.claim_id)
            self._require_claim_scope(claim, base_scope)
            observations = self._source_observations(
                unit_of_work,
                request.source_observation_ids,
                base_scope,
            )
            scope = self._scope_with_write(base_scope, claim.namespace)
            command = self._prepare_command(
                unit_of_work,
                tool_name="memory.claim.supersede",
                scope=scope,
                payload=request.model_dump(mode="json"),
                idempotency_key=request.idempotency_key,
                approval_confirmed=True,
            )
            source_event = self._command_source_event(unit_of_work, command.run.id)
            revision = self._revision_from_request(
                claim=claim,
                expected_revision=request.expected_revision,
                observations=observations,
                source_event=source_event,
                value=request.value,
                normalized_text=request.normalized_text,
                valid_from=request.valid_from,
                valid_to=request.valid_to,
            )
            claim = unit_of_work.memory.append_revision(
                claim.id,
                expected_revision=request.expected_revision,
                revision=revision,
                request_fingerprint=self._stage_fingerprint(command.run.id, "revision"),
            )
            context = self._claim_context(unit_of_work, claim)
            if command.execute:
                self._finish(
                    unit_of_work,
                    command.run,
                    event_type="memory.claim.superseded",
                    message="Memory claim superseded",
                    payload={
                        "claim_id": str(claim.id),
                        "revision": context.current_revision.revision,
                        "superseded_revision": request.expected_revision,
                    },
                )
            unit_of_work.commit()
            source_run_id = command.run.id
        self._refresh_projection_after_commit(request.task_id, source_run_id)
        return context

    def resolve_conflict(
        self,
        request: MemoryClaimResolveInput,
    ) -> MemoryClaimContext:
        self._require_confirmation(request.user_confirmed)
        self._require_safe_claim_value(request.value, request.normalized_text)
        with self._unit_of_work_factory() as unit_of_work:
            _task, base_scope = self._task_scope(unit_of_work, request.task_id)
            claim = self._require_claim(unit_of_work, request.claim_id)
            self._require_claim_scope(claim, base_scope)
            for resolved_id in request.resolved_claim_ids:
                resolved = self._require_claim(unit_of_work, resolved_id)
                self._require_claim_scope(resolved, base_scope)
            observations = self._source_observations(
                unit_of_work,
                request.source_observation_ids,
                base_scope,
            )
            scope = self._scope_with_write(base_scope, claim.namespace)
            command = self._prepare_command(
                unit_of_work,
                tool_name="memory.claim.resolve_conflict",
                scope=scope,
                payload=request.model_dump(mode="json"),
                idempotency_key=request.idempotency_key,
                approval_confirmed=True,
            )
            source_event = self._command_source_event(unit_of_work, command.run.id)
            revision = self._revision_from_request(
                claim=claim,
                expected_revision=request.expected_revision,
                observations=observations,
                source_event=source_event,
                value=request.value,
                normalized_text=request.normalized_text,
                valid_from=request.valid_from,
                valid_to=request.valid_to,
                resolved_claim_ids=request.resolved_claim_ids,
            )
            claim = unit_of_work.memory.resolve_conflict(
                claim.id,
                expected_revision=request.expected_revision,
                revision=revision,
                resolved_claim_ids=request.resolved_claim_ids,
                request_fingerprint=self._stage_fingerprint(command.run.id, "revision"),
            )
            context = self._claim_context(unit_of_work, claim)
            if command.execute:
                self._finish(
                    unit_of_work,
                    command.run,
                    event_type="memory.claim.resolved",
                    message="Memory claim conflict resolved",
                    payload={
                        "claim_id": str(claim.id),
                        "revision": context.current_revision.revision,
                        "resolved_claim_ids": [str(value) for value in request.resolved_claim_ids],
                    },
                )
            unit_of_work.commit()
            source_run_id = command.run.id
        self._refresh_projection_after_commit(request.task_id, source_run_id)
        return context

    def forget(self, request: MemoryForgetInput) -> MemoryTombstone:
        self._require_confirmation(request.user_confirmed)
        with self._unit_of_work_factory() as unit_of_work:
            _task, base_scope = self._task_scope(unit_of_work, request.task_id)
            target_kind = MemoryTargetKind(request.target_kind.value)
            if target_kind is MemoryTargetKind.CLAIM:
                target = self._require_claim(
                    unit_of_work,
                    request.target_id,
                    include_forgotten=True,
                )
                namespace = target.namespace
                self._require_claim_scope(target, base_scope)
            else:
                target = self._require_observation(
                    unit_of_work,
                    request.target_id,
                    include_forgotten=True,
                )
                namespace = target.proposed_namespace
                self._require_observation_identity(target, base_scope)
            scope = self._scope_with_write(base_scope, namespace)
            command = self._prepare_command(
                unit_of_work,
                tool_name="memory.forget",
                scope=scope,
                payload=request.model_dump(mode="json"),
                idempotency_key=request.idempotency_key,
                approval_confirmed=True,
            )
            source_event = self._command_source_event(unit_of_work, command.run.id)
            tombstone = MemoryTombstone.create(
                target_kind=target_kind,
                target_id=request.target_id,
                reason=request.reason,
                actor=_USER_ACTOR,
                source_event_id=source_event.id,
            )
            persisted = unit_of_work.memory.forget(
                tombstone,
                request_fingerprint=self._stage_fingerprint(command.run.id, "tombstone"),
            )
            if command.execute:
                self._finish(
                    unit_of_work,
                    command.run,
                    event_type=f"memory.{target_kind.value}.forgotten",
                    message="Memory forgotten",
                    payload={
                        "target_kind": target_kind.value,
                        "target_id": str(request.target_id),
                        "tombstone_id": str(persisted.id),
                    },
                )
            unit_of_work.commit()
            source_run_id = command.run.id
        self._refresh_projection_after_commit(request.task_id, source_run_id)
        return persisted

    def _refresh_projection_after_commit(
        self,
        task_id: UUID,
        source_run_id: UUID,
    ) -> None:
        running = self._start_projection_refresh(task_id, source_run_id)
        if running is None:
            return
        try:
            with self._unit_of_work_factory() as unit_of_work:
                persisted = unit_of_work.commands.get_run(running.id)
                if persisted is None or persisted.status is CommandStatus.SUCCEEDED:
                    return
                if persisted.status is not CommandStatus.RUNNING:
                    return
                refresher = LexicalProjectionRefresher(
                    memory_repository=unit_of_work.memory,
                    projection_writer=unit_of_work.memory_projections,
                    command_ledger=unit_of_work.commands,
                    memory_policy=self._memory_policy,
                )
                health = refresher.refresh()
                bus = CommandBus(
                    registry=self._registry,
                    policy=self._command_policy,
                    ledger=unit_of_work.commands,
                )
                bus.complete(
                    persisted.id,
                    output={
                        "generation": health.generation,
                        "projected_watermark_cursor": health.projected_watermark_cursor,
                    },
                    lease_owner=persisted.lease_owner,
                    lease_fence=persisted.lease_fence,
                )
                unit_of_work.memory_projections.advance_checkpoint(
                    generation=health.generation,
                    source_watermark_cursor=unit_of_work.commands.current_cursor(),
                )
                unit_of_work.commit()
        except Exception as error:
            self._fail_projection_refresh(running, error)

    def _start_projection_refresh(
        self,
        task_id: UUID,
        source_run_id: UUID,
    ) -> CommandRun | None:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                _task, scope = self._task_scope(unit_of_work, task_id)
                bus = CommandBus(
                    registry=self._registry,
                    policy=self._command_policy,
                    ledger=unit_of_work.commands,
                )
                dispatch = bus.submit(
                    CommandRequest(
                        tool_name="memory.projection.refresh",
                        actor="core",
                        scope=scope,
                        payload={
                            "source_run_id": str(source_run_id),
                            "source_watermark_cursor": (unit_of_work.commands.current_cursor()),
                        },
                        idempotency_key=f"memory-projection:{source_run_id}",
                    ),
                    profile=PermissionProfile.STANDARD,
                    capability_overrides={},
                    sandbox_healthy=False,
                )
                if dispatch.run is None or not dispatch.accepted:
                    return None
                if dispatch.run.status is CommandStatus.SUCCEEDED:
                    return None
                try:
                    running = bus.start(dispatch.run.id)
                except InvalidTransitionError:
                    return None
                unit_of_work.commit()
                return running
        except Exception:
            return None

    def _fail_projection_refresh(
        self,
        running: CommandRun,
        error: Exception,
    ) -> None:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                persisted = unit_of_work.commands.get_run(running.id)
                if persisted is None or persisted.status is not CommandStatus.RUNNING:
                    return
                bus = CommandBus(
                    registry=self._registry,
                    policy=self._command_policy,
                    ledger=unit_of_work.commands,
                )
                bus.fail(
                    persisted.id,
                    error_code=str(
                        getattr(error, "code", getattr(error, "error_code", "WORKER_INTERRUPTED"))
                    ),
                    lease_owner=persisted.lease_owner,
                    lease_fence=persisted.lease_fence,
                )
                unit_of_work.commit()
        except Exception:
            return

    def _prepare_command(
        self,
        unit_of_work: CoreUnitOfWork,
        *,
        tool_name: str,
        scope: ScopeContract,
        payload: dict[str, object],
        idempotency_key: str,
        approval_confirmed: bool,
    ) -> _PreparedCommand:
        bus = CommandBus(
            registry=self._registry,
            policy=self._command_policy,
            ledger=unit_of_work.commands,
        )
        dispatch = bus.submit(
            CommandRequest(
                tool_name=tool_name,
                actor=_USER_ACTOR,
                scope=scope,
                payload=payload,
                idempotency_key=idempotency_key.strip(),
            ),
            profile=PermissionProfile.STANDARD,
            capability_overrides={},
            sandbox_healthy=False,
        )
        if dispatch.run is None or not dispatch.accepted:
            raise MemoryConflictError(dispatch.reason or dispatch.error_code or "command rejected")
        run = dispatch.run
        if run.status is CommandStatus.SUCCEEDED:
            return _PreparedCommand(run, False)
        if dispatch.requires_approval:
            if not approval_confirmed:
                raise ApprovalRequiredError(dispatch.reason or "explicit approval required")
            run = bus.decide_approval(run.id, approved=True)
        if run.status in {CommandStatus.QUEUED, CommandStatus.RUNNING}:
            try:
                return _PreparedCommand(bus.start(run.id), True)
            except InvalidTransitionError as error:
                raise MemoryConflictError(
                    "memory command is already running with an active lease"
                ) from error
        raise MemoryConflictError(f"memory command cannot execute from {run.status}")

    @staticmethod
    def _finish(
        unit_of_work: CoreUnitOfWork,
        run: CommandRun,
        *,
        event_type: str,
        message: str,
        payload: dict[str, object],
    ) -> None:
        unit_of_work.commands.finish(
            run.id,
            status=CommandStatus.SUCCEEDED,
            event_type=event_type,
            visibility=EventVisibility.USER,
            message=message,
            payload=payload,
            lease_owner=run.lease_owner,
            lease_fence=run.lease_fence,
        )

    def _task_scope(
        self,
        unit_of_work: CoreUnitOfWork,
        task_id: UUID,
    ) -> tuple[Task, ScopeContract]:
        task = unit_of_work.state.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task, self._scope_resolver(unit_of_work.state, task)

    @staticmethod
    def _scope_with_write(
        scope: ScopeContract,
        namespace: MemoryNamespace,
    ) -> ScopeContract:
        return ScopeContract.create(
            workspace_type=scope.workspace_type,
            project_id=scope.project_id,
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            operation_mode=scope.operation_mode,
            base_version_id=scope.base_version_id,
            target_version_id=scope.target_version_id,
            project_root=scope.project_root,
            allowed_write_paths=scope.allowed_write_paths,
            forbidden_write_paths=scope.forbidden_write_paths,
            execution_target=scope.execution_target,
            network_policy=scope.network_policy,
            memory_read_scope=scope.memory_read_scope,
            memory_write_scope=(namespace.value,),
            memory_snapshot_id=scope.memory_snapshot_id,
            memory_snapshot_hash=scope.memory_snapshot_hash,
        )

    def _require_observation_scope(
        self,
        observation: MemoryObservation,
        base_scope: ScopeContract,
    ) -> None:
        self._require_observation_identity(observation, base_scope)
        source_scope = self._scope_with_write(base_scope, observation.proposed_namespace)
        decision = self._memory_policy.evaluate_promotion(
            observation,
            observation.proposed_namespace,
            source_scope,
        )
        self._require_policy_allowed(decision)

    @staticmethod
    def _require_observation_identity(
        observation: MemoryObservation,
        scope: ScopeContract,
    ) -> None:
        expected_version = scope.target_version_id or scope.base_version_id
        if (
            observation.project_id != scope.project_id
            or observation.conversation_id != scope.conversation_id
            or observation.task_id != scope.task_id
            or observation.version_id != expected_version
        ):
            raise MemoryScopeViolationError("Observation is outside the current Task Scope")

    def _require_claim_scope(self, claim: MemoryClaim, scope: ScopeContract) -> None:
        self._require_read_namespace(scope, claim.namespace)
        matches = {
            MemoryNamespace.PROJECT_CANONICAL: claim.project_id == scope.project_id,
            MemoryNamespace.CONVERSATION_DRAFT: (claim.conversation_id == scope.conversation_id),
            MemoryNamespace.USER_PROFILE: True,
            MemoryNamespace.DEVICE_LOCAL: False,
            MemoryNamespace.TASK_EPISODE: claim.task_id == scope.task_id,
        }[claim.namespace]
        if not matches:
            raise MemoryScopeViolationError("Claim is outside the current Task Scope")

    @staticmethod
    def _scope_arguments(
        scope: ScopeContract,
        namespace: MemoryNamespace,
    ) -> dict[str, UUID | str | None]:
        if namespace is MemoryNamespace.PROJECT_CANONICAL:
            return {
                "project_id": scope.project_id,
                "conversation_id": None,
                "task_id": None,
                "device_id": None,
            }
        if namespace is MemoryNamespace.CONVERSATION_DRAFT:
            return {
                "project_id": None,
                "conversation_id": scope.conversation_id,
                "task_id": None,
                "device_id": None,
            }
        if namespace is MemoryNamespace.TASK_EPISODE:
            return {
                "project_id": None,
                "conversation_id": None,
                "task_id": scope.task_id,
                "device_id": None,
            }
        return {
            "project_id": None,
            "conversation_id": None,
            "task_id": None,
            "device_id": None,
        }

    @staticmethod
    def _require_read_namespace(scope: ScopeContract, namespace: MemoryNamespace) -> None:
        if not _READ_SCOPE_ALIASES[namespace].intersection(scope.memory_read_scope):
            raise MemoryScopeViolationError(
                f"{namespace.value} is not readable in the current Task Scope"
            )

    @staticmethod
    def _require_observation(
        unit_of_work: CoreUnitOfWork,
        observation_id: UUID,
        *,
        include_forgotten: bool = False,
    ) -> MemoryObservation:
        observation = unit_of_work.memory.get_observation(
            observation_id,
            include_forgotten=include_forgotten,
        )
        if observation is None:
            raise KeyError(f"memory Observation not found: {observation_id}")
        return observation

    @staticmethod
    def _require_claim(
        unit_of_work: CoreUnitOfWork,
        claim_id: UUID,
        *,
        include_forgotten: bool = False,
    ) -> MemoryClaim:
        claim = unit_of_work.memory.get_claim(
            claim_id,
            include_forgotten=include_forgotten,
        )
        if claim is None:
            raise KeyError(f"memory Claim not found: {claim_id}")
        return claim

    def _source_observations(
        self,
        unit_of_work: CoreUnitOfWork,
        observation_ids: tuple[UUID, ...],
        scope: ScopeContract,
    ) -> tuple[MemoryObservation, ...]:
        observations = tuple(
            self._require_observation(unit_of_work, observation_id)
            for observation_id in observation_ids
        )
        for observation in observations:
            self._require_observation_scope(observation, scope)
        return observations

    @staticmethod
    def _claim_context(
        unit_of_work: CoreUnitOfWork,
        claim: MemoryClaim,
    ) -> MemoryClaimContext:
        revisions = unit_of_work.memory.revisions_for_claim(claim.id)
        current = next(
            (revision for revision in revisions if revision.revision == claim.current_revision),
            None,
        )
        if current is None:
            raise MemoryConflictError("Claim has no current revision")
        return MemoryClaimContext(claim=claim, current_revision=current)

    @staticmethod
    def _revision_from_request(
        *,
        claim: MemoryClaim,
        expected_revision: int,
        observations: tuple[MemoryObservation, ...],
        source_event: EventEnvelope,
        value: object,
        normalized_text: str,
        valid_from,
        valid_to,
        resolved_claim_ids: tuple[UUID, ...] = (),
    ) -> MemoryClaimRevision:
        return MemoryClaimRevision.create(
            claim_id=claim.id,
            revision=expected_revision + 1,
            value=value,
            normalized_text=normalized_text,
            source_observation_ids=tuple(item.id for item in observations),
            source_event_ids=MemoryApplication._source_event_ids(
                observations,
                source_event,
            ),
            authority=MemoryAuthority.EXPLICIT_USER,
            confidence=min(item.confidence for item in observations),
            valid_from=valid_from,
            valid_to=valid_to,
            actor=_USER_ACTOR,
            supersedes_revision=expected_revision,
            resolved_claim_ids=resolved_claim_ids,
        )

    @staticmethod
    def _source_event_ids(
        observations: tuple[MemoryObservation, ...],
        command_event: EventEnvelope,
    ) -> tuple[UUID, ...]:
        return tuple(
            dict.fromkeys([*(item.source_event_id for item in observations), command_event.id])
        )

    @staticmethod
    def _command_source_event(
        unit_of_work: CoreUnitOfWork,
        run_id: UUID,
    ) -> EventEnvelope:
        event = next(
            (
                item
                for item in unit_of_work.commands.events_for_run(run_id)
                if item.event_type == "command.created"
            ),
            None,
        )
        if event is None:
            raise MemoryConflictError("memory Command has no durable source event")
        return event

    def _require_safe_content(self, content: str) -> None:
        self._require_policy_allowed(self._memory_policy.scan_content(content))

    def _require_safe_claim_value(self, value: object, normalized_text: str) -> None:
        rendered = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._require_safe_content(f"{normalized_text}\n{rendered}")

    @staticmethod
    def _require_policy_allowed(decision: MemoryPolicyDecision) -> None:
        if decision.allowed:
            return
        errors = {
            "MEMORY_SCOPE_VIOLATION": MemoryScopeViolationError,
            "MEMORY_INJECTION_BLOCKED": MemoryInjectionBlockedError,
            "MEMORY_SECRET_BLOCKED": MemorySecretBlockedError,
            "MEMORY_FORGOTTEN": MemoryForgottenError,
        }
        if decision.requires_approval or decision.error_code == "APPROVAL_REQUIRED":
            raise ApprovalRequiredError(decision.reason or "explicit approval required")
        error_type = errors.get(decision.error_code, MemoryConflictError)
        raise error_type(decision.reason or decision.error_code or "memory policy rejected")

    @staticmethod
    def _require_confirmation(user_confirmed: bool) -> None:
        if not user_confirmed:
            raise ApprovalRequiredError("explicit user confirmation is required")

    @staticmethod
    def _stage_fingerprint(run_id: UUID, stage: str) -> str:
        return hashlib.sha256(f"fairy:v3:memory:{run_id}:{stage}".encode()).hexdigest()


__all__ = ["MemoryApplication", "MemoryClaimContext", "ScopeResolver"]

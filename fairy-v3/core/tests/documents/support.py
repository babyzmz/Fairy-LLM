from __future__ import annotations

import hashlib
from pathlib import Path

from fairy_core.application.core import CoreApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import ExecutionTarget, TaskCreate
from fairy_core.documents import (
    ExtractedDocument,
    ExtractedSection,
    StoredDocumentBlob,
)
from fairy_core.documents.application import DocumentApplication
from fairy_core.domain.models import (
    OperationMode,
    ProjectResidency,
    WorkspaceType,
)
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


class FixtureDocumentParser:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []

    def parse(self, *, filename: str, media_type: str, content: bytes) -> ExtractedDocument:
        self.calls.append((filename, media_type, content))
        text = content.decode("utf-8")
        sections = tuple(
            ExtractedSection(
                ordinal=index,
                title=f"Section {index + 1}",
                text=value.strip(),
                locator={"section": index + 1},
            )
            for index, value in enumerate(text.split("\n---\n"))
            if value.strip()
        )
        return ExtractedDocument(
            media_type=media_type,
            parser="fixture",
            parser_version="1",
            sections=sections,
        )


class RecordingDocumentBlobStore:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []
        self.content: dict[str, bytes] = {}

    def put(
        self,
        *,
        content_hash: str,
        content: bytes,
        media_type: str,
    ) -> StoredDocumentBlob:
        self.puts.append((content_hash, content, media_type))
        self.content[content_hash] = content
        return StoredDocumentBlob(
            storage_location=f"managed://sha256/{content_hash}",
            content_hash=content_hash,
            byte_length=len(content),
        )

    def read(self, blob: StoredDocumentBlob) -> bytes:
        content = self.content[blob.content_hash]
        assert hashlib.sha256(content).hexdigest() == blob.content_hash
        return content


def applications(tmp_path: Path, *, tenant_id: str = "local"):
    engine = create_sqlite_core_engine(tmp_path / "core.db", tenant_id=tenant_id)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    registry = build_default_registry()
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / "managed"),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    parser = FixtureDocumentParser()
    blobs = RecordingDocumentBlobStore()
    documents = DocumentApplication(
        unit_of_work_factory=factory,
        registry=registry,
        command_policy=PolicyEngine(registry),
        scope_resolver=core.scope_for_task,
        parser=parser,
        blob_store=blobs,
    )
    return engine, factory, core, documents, parser, blobs


def project_task(core: CoreApplication, *, suffix: str = "one", project_id=None):
    if project_id is None:
        project = core.create_project(
            name=f"Documents {suffix}",
            residency=ProjectResidency.LOCAL_ONLY,
        ).project
        project_id = project.id
    conversation = core.create_conversation(
        project_id=project_id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    return core.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Use managed documents",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key=f"documents:task:{suffix}",
        )
    )


def scratch_task(core: CoreApplication, *, suffix: str = "scratch"):
    conversation = core.create_conversation(
        project_id=None,
        workspace_type=WorkspaceType.CHAT_SCRATCH,
    )
    return core.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Use a scratch document",
            operation_mode=OperationMode.ANSWER,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key=f"documents:task:{suffix}",
        )
    )

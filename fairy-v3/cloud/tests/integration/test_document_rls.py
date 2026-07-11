from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import (
    DocumentImportInput,
    DocumentSearchInput,
    ExecutionTarget,
    TaskCreate,
)
from fairy_core.documents import (
    DocumentVisibility,
    ExtractedDocument,
    ExtractedSection,
    StoredDocumentBlob,
)
from fairy_core.documents.application import DocumentApplication
from fairy_core.domain.models import OperationMode, WorkspaceType
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


class TextParser:
    def parse(self, *, filename: str, media_type: str, content: bytes) -> ExtractedDocument:
        del filename
        return ExtractedDocument(
            media_type=media_type,
            parser="integration",
            parser_version="1",
            sections=(
                ExtractedSection(
                    ordinal=0,
                    title="Document",
                    text=content.decode(),
                    locator={"section": 1},
                ),
            ),
        )


class FixtureBlobStore:
    def put(
        self,
        *,
        content_hash: str,
        content: bytes,
        media_type: str,
    ) -> StoredDocumentBlob:
        del media_type
        return StoredDocumentBlob(
            storage_location=f"s3://test/documents/{content_hash}",
            content_hash=content_hash,
            byte_length=len(content),
        )

    def read(self, blob: StoredDocumentBlob) -> bytes:
        raise AssertionError(f"read should not be needed: {blob.content_hash}")


def test_postgres_documents_search_vector_and_rows_are_tenant_scoped(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_a = postgres_test_context.track_tenant(f"documents-a-{uuid4().hex}")
    tenant_b = postgres_test_context.track_tenant(f"documents-b-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    try:
        core_a, documents_a, _factory_a = _stack(engine, tmp_path, tenant_a)
        _core_b, _documents_b, factory_b = _stack(engine, tmp_path, tenant_b)
        conversation = core_a.create_conversation(
            project_id=None,
            workspace_type=WorkspaceType.CHAT_SCRATCH,
        )
        task = core_a.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Search tenant evidence",
                operation_mode=OperationMode.ANSWER,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="documents:rls:task",
            )
        ).task
        content = b"Tenant lexical sentinel"
        imported = documents_a.import_document(
            DocumentImportInput(
                task_id=task.id,
                filename="tenant.txt",
                media_type="text/plain",
                content_base64=base64.b64encode(content).decode(),
                visibility=DocumentVisibility.CONVERSATION,
                idempotency_key="documents:rls:import",
                user_confirmed=True,
            )
        )

        hits = documents_a.search_documents(
            DocumentSearchInput(task_id=task.id, query="sentinel", limit=10)
        ).items
        assert [hit.document.id for hit in hits] == [imported.document.id]
        assert hits[0].revision.content_hash == hashlib.sha256(content).hexdigest()
        with factory_b() as unit_of_work:
            assert unit_of_work.documents.get_context(imported.document.id) is None
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_b},
            )
            assert (
                connection.execute(
                    text("SELECT id FROM core_documents WHERE id = :document_id"),
                    {"document_id": str(imported.document.id)},
                ).scalar_one_or_none()
                is None
            )
            transaction.rollback()
    finally:
        engine.dispose()


def _stack(engine, root: Path, tenant_id: str):
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    registry = build_default_registry()
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(root / tenant_id),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    documents = DocumentApplication(
        unit_of_work_factory=factory,
        registry=registry,
        command_policy=PolicyEngine(registry),
        scope_resolver=core.scope_for_task,
        parser=TextParser(),
        blob_store=FixtureBlobStore(),
    )
    return core, documents, factory

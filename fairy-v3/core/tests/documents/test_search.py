from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

from sqlalchemy import update

from fairy_core.contracts.models import (
    DocumentImportInput,
    DocumentSearchInput,
)
from fairy_core.documents import (
    DocumentVisibility,
    ExtractedDocument,
    ExtractedSection,
    ManagedDocument,
    StoredDocumentBlob,
)
from fairy_core.documents.search import chunk_extracted_document
from fairy_core.domain.ids import new_id
from fairy_core.storage.schema import documents as document_table

from .support import applications, project_task, scratch_task


def _import(documents, task_id, *, text: str, visibility, key: str):
    return documents.import_document(
        DocumentImportInput(
            task_id=task_id,
            filename="knowledge.txt",
            media_type="text/plain",
            content_base64=base64.b64encode(text.encode()).decode("ascii"),
            visibility=visibility,
            idempotency_key=key,
            user_confirmed=True,
        )
    )


def _search(documents, task_id, query: str):
    return documents.search_documents(
        DocumentSearchInput(task_id=task_id, query=query, limit=20)
    ).items


def test_chunk_boundaries_hashes_and_locators_are_deterministic_across_document_ids() -> None:
    content = ("alpha beta gamma " * 240).strip()
    digest = hashlib.sha256(content.encode()).hexdigest()
    blob = StoredDocumentBlob(
        storage_location=f"managed://sha256/{digest}",
        content_hash=digest,
        byte_length=len(content.encode()),
    )
    extracted = ExtractedDocument(
        media_type="text/plain",
        parser="fixture",
        parser_version="1",
        sections=(
            ExtractedSection(
                ordinal=0,
                title="Evidence",
                text=content,
                locator={"section": 1},
            ),
        ),
    )
    now = datetime(2026, 7, 11, tzinfo=UTC)

    def chunks_for_document():
        document = ManagedDocument.create(
            project_id=new_id(),
            conversation_id=new_id(),
            source_task_id=new_id(),
            version_id=new_id(),
            filename="evidence.txt",
            media_type="text/plain",
            blob=blob,
            visibility=DocumentVisibility.PROJECT,
            idempotency_key=str(new_id()),
            now=now,
        )
        return tuple(
            (
                chunk.ordinal,
                chunk.section_ordinal,
                dict(chunk.locator),
                chunk.text,
                chunk.content_hash,
                chunk.token_count,
            )
            for chunk in chunk_extracted_document(document, extracted)
        )

    assert chunks_for_document() == chunks_for_document()


def test_search_is_deterministic_and_enforces_project_conversation_isolation(tmp_path) -> None:
    _engine, _factory, core, documents, _parser, _blobs = applications(tmp_path)
    source = project_task(core, suffix="source")
    same_project = project_task(
        core,
        suffix="same-project",
        project_id=source.task.project_id,
    )
    other_project = project_task(core, suffix="other-project")
    scratch = scratch_task(core)
    project_doc = _import(
        documents,
        source.task.id,
        text="Project atlas canonical evidence",
        visibility=DocumentVisibility.PROJECT,
        key="documents:import:project",
    )
    conversation_doc = _import(
        documents,
        source.task.id,
        text="Conversation comet private evidence",
        visibility=DocumentVisibility.CONVERSATION,
        key="documents:import:conversation",
    )

    first = _search(documents, source.task.id, "evidence")
    second = _search(documents, source.task.id, "evidence")
    assert [hit.chunk.content_hash for hit in first] == [hit.chunk.content_hash for hit in second]
    assert {hit.document.id for hit in first} == {
        project_doc.document.id,
        conversation_doc.document.id,
    }
    assert {hit.document.id for hit in _search(documents, same_project.task.id, "evidence")} == {
        project_doc.document.id
    }
    assert _search(documents, other_project.task.id, "evidence") == ()
    assert _search(documents, scratch.task.id, "evidence") == ()


def test_search_revalidates_revision_and_hash_before_returning_projection(tmp_path) -> None:
    engine, _factory, core, documents, _parser, _blobs = applications(tmp_path)
    task = project_task(core, suffix="stale")
    context = _import(
        documents,
        task.task.id,
        text="Stale projection sentinel",
        visibility=DocumentVisibility.CONVERSATION,
        key="documents:import:stale",
    )
    assert _search(documents, task.task.id, "sentinel")
    replacement_hash = hashlib.sha256(b"replacement revision").hexdigest()

    with engine.begin() as connection:
        connection.execute(
            update(document_table)
            .where(
                document_table.c.tenant_id == "local",
                document_table.c.id == str(context.document.id),
            )
            .values(current_revision=2, content_hash=replacement_hash)
        )

    assert _search(documents, task.task.id, "sentinel") == ()

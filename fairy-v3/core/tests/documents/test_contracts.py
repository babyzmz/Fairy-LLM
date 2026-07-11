from __future__ import annotations

import base64

from fairy_core.transports.stdio import build_local_service

from .support import FixtureDocumentParser, RecordingDocumentBlobStore


def test_core_service_exposes_document_round_trip_without_storage_location(tmp_path) -> None:
    service = build_local_service(
        tmp_path,
        document_parser=FixtureDocumentParser(),
        document_blob_store=RecordingDocumentBlobStore(),
    )
    try:
        project = service.invoke(
            "projects.create",
            {"name": "Document contracts", "residency": "local_only"},
        )["project"]
        conversation = service.invoke(
            "conversations.create",
            {"project_id": project["id"], "workspace_type": "project_chat"},
        )
        task = service.invoke(
            "tasks.create",
            {
                "conversation_id": conversation["id"],
                "user_request": "Read managed evidence",
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "local",
                "idempotency_key": "documents:contract:task",
            },
        )["task"]
        imported = service.invoke(
            "documents.import",
            {
                "task_id": task["id"],
                "filename": "evidence.txt",
                "media_type": "text/plain",
                "content_base64": base64.b64encode(b"Managed evidence sentinel").decode(),
                "visibility": "conversation",
                "idempotency_key": "documents:contract:import",
                "user_confirmed": True,
            },
        )

        assert "storage_location" not in imported["document"]
        assert (
            service.invoke(
                "documents.get",
                {"task_id": task["id"], "document_id": imported["document"]["id"]},
            )
            == imported
        )
        assert service.invoke("documents.list", {"task_id": task["id"]})["items"] == [imported]
        hits = service.invoke(
            "documents.search",
            {"task_id": task["id"], "query": "sentinel", "limit": 10},
        )["items"]
        assert hits[0]["document"] == imported["document"]
        assert hits[0]["chunk"]["text"] == "Managed evidence sentinel"
        deleted = service.invoke(
            "documents.delete",
            {
                "task_id": task["id"],
                "document_id": imported["document"]["id"],
                "idempotency_key": "documents:contract:delete",
                "user_confirmed": True,
            },
        )
        assert deleted["document"]["status"] == "deleted"
    finally:
        service.close()

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from fairy_core.domain.errors import (
    CommandRejectedError,
    IdempotencyConflictError,
    PreviewScopeViolationError,
    VersionConflictError,
)
from fairy_core.presentation.cache import presentation_cache_key
from fairy_core.presentation.models import FileRenderJob, RenderJobStatus
from fairy_core.presentation.packs import (
    RendererPackInstaller,
    RendererPackVerificationError,
)
from fairy_core.transports.stdio import build_local_service


def _job() -> FileRenderJob:
    return FileRenderJob.create(
        workspace_id=uuid4(),
        version_id=uuid4(),
        file_set_id=uuid4(),
        source_path="model.obj",
        source_hash="a" * 64,
        cache_key="b" * 64,
        requested_mode="auto",
        renderer_pack_id="cad",
        renderer_pack_version="1",
    )


def test_render_job_rejects_illegal_or_ambiguous_terminal_transitions() -> None:
    job = _job()
    queued = job.transition(RenderJobStatus.QUEUED, progress=5)
    converting = queued.transition(RenderJobStatus.CONVERTING, progress=20)
    validating = converting.transition(RenderJobStatus.VALIDATING, progress=90)
    ready = validating.transition(RenderJobStatus.READY)

    assert ready.progress == 100
    with pytest.raises(ValueError, match="invalid render job transition"):
        ready.transition(RenderJobStatus.CONVERTING)
    with pytest.raises(ValueError, match="require an error code"):
        converting.transition(RenderJobStatus.FAILED)


def test_presentation_cache_key_is_dependency_order_independent_and_parameter_bound() -> None:
    common = {
        "source_hash": "a" * 64,
        "renderer_pack_id": "office",
        "renderer_pack_version": "2.1",
        "platform": "windows",
        "color_configuration": "srgb-v1",
    }
    first = presentation_cache_key(
        **common,
        dependency_hashes=["b" * 64, "c" * 64],
        parameters={"mode": "normalized", "dpi": 144},
    )
    reordered = presentation_cache_key(
        **common,
        dependency_hashes=["c" * 64, "b" * 64],
        parameters={"dpi": 144, "mode": "normalized"},
    )
    changed = presentation_cache_key(
        **common,
        dependency_hashes=["b" * 64, "c" * 64],
        parameters={"mode": "normalized", "dpi": 96},
    )

    assert first == reordered
    assert first != changed


def test_service_persists_native_presentation_and_deduplicates_cache(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "notes.txt").write_text("Fairy presentation", encoding="utf-8")
    service = build_local_service(tmp_path / "app")
    try:
        imported = service.invoke(
            "projects.import",
            {"name": "Files", "residency": "local_only", "source_path": str(source)},
        )
        request = {
            "workspace_id": imported["project"]["workspace_id"],
            "version_id": imported["project"]["active_version_id"],
            "path": "notes.txt",
        }
        first = service.invoke("files.present", request)
        second = service.invoke("files.present", request)

        assert first == second
        assert first["job"]["status"] == "ready"
        assert first["presentation"]["renderer"] == "browser-native"
        assert first["presentation"]["capabilities"] == ["search", "select", "copy"]
    finally:
        service.close()


def test_service_builds_content_only_preview_for_modern_office_package(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with zipfile.ZipFile(source / "report.docx", "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="urn:w"><w:p><w:r>'
            "<w:t>Quarterly review</w:t></w:r></w:p></w:document>",
        )
    service = build_local_service(tmp_path / "app")
    try:
        imported = service.invoke(
            "projects.import",
            {"name": "Office", "residency": "local_only", "source_path": str(source)},
        )
        result = service.invoke(
            "files.present",
            {
                "workspace_id": imported["project"]["workspace_id"],
                "version_id": imported["project"]["active_version_id"],
                "path": "report.docx",
            },
        )

        assert result["job"]["status"] == "ready"
        assert result["job"]["renderer_pack_id"] is None
        assert result["presentation"]["fidelity"] == "content_only"
        assert result["presentation"]["assets"][0]["metadata"]["payload"] == {
            "kind": "document",
            "blocks": [{"kind": "paragraph", "text": "Quarterly review"}],
        }
    finally:
        service.close()


def test_service_keeps_legacy_office_binary_behind_renderer_pack(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "legacy.doc").write_bytes(b"\xd0\xcf\x11\xe0legacy")
    service = build_local_service(tmp_path / "app")
    try:
        imported = service.invoke(
            "projects.import",
            {"name": "Legacy Office", "residency": "local_only", "source_path": str(source)},
        )
        result = service.invoke(
            "files.present",
            {
                "workspace_id": imported["project"]["workspace_id"],
                "version_id": imported["project"]["active_version_id"],
                "path": "legacy.doc",
            },
        )

        assert result["job"]["status"] == "waiting_for_pack"
        assert result["job"]["renderer_pack_id"] == "office"
        assert result["presentation"] is None
    finally:
        service.close()


@pytest.mark.parametrize(
    ("filename", "expected_pack"),
    [("assembly.step", "cad"), ("building.ifc", "bim"), ("scene.fbx", "dcc-3d")],
)
def test_professional_3d_formats_require_their_explicit_pack(
    tmp_path: Path, filename: str, expected_pack: str
) -> None:
    source = tmp_path / f"source-{expected_pack}"
    source.mkdir()
    (source / filename).write_bytes(b"professional-model-fixture")
    service = build_local_service(tmp_path / f"app-{expected_pack}")
    try:
        imported = service.invoke(
            "projects.import",
            {"name": "Model", "residency": "local_only", "source_path": str(source)},
        )
        result = service.invoke(
            "files.present",
            {
                "workspace_id": imported["project"]["workspace_id"],
                "version_id": imported["project"]["active_version_id"],
                "path": filename,
            },
        )

        assert result["job"]["status"] == "waiting_for_pack"
        assert result["job"]["renderer_pack_id"] == expected_pack
        assert result["presentation"] is None
    finally:
        service.close()


def test_document_package_rejects_unsafe_xml_without_exposing_content(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with zipfile.ZipFile(source / "unsafe.docx", "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            '<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]><document>&leak;</document>',
        )
    service = build_local_service(tmp_path / "app")
    try:
        imported = service.invoke(
            "projects.import",
            {"name": "Unsafe Office", "residency": "local_only", "source_path": str(source)},
        )
        result = service.invoke(
            "files.present",
            {
                "workspace_id": imported["project"]["workspace_id"],
                "version_id": imported["project"]["active_version_id"],
                "path": "unsafe.docx",
            },
        )

        assert result["job"]["status"] == "quarantined"
        assert result["job"]["error_code"] == "DOCUMENT_PACKAGE_INVALID"
        assert result["presentation"] is None
    finally:
        service.close()


def test_generated_asset_sets_bind_variants_and_safe_provenance_to_version(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    service = build_local_service(tmp_path / "app")
    try:
        imported = service.invoke(
            "projects.import",
            {"name": "Generated", "residency": "local_only", "source_path": str(source)},
        )
        workspace_id = imported["project"]["workspace_id"]
        version_id = imported["project"]["active_version_id"]
        files = service.invoke(
            "workspaces.files.list",
            {"workspace_id": workspace_id, "version_id": version_id},
        )
        image = next(item for item in files["items"] if item["path"] == "hero.png")
        created = service.invoke(
            "asset_sets.create",
            {
                "workspace_id": workspace_id,
                "version_id": version_id,
                "idempotency_key": "asset-set:landing-hero",
                "kind": "image",
                "title": "Landing hero variants",
                "variants": [
                    {
                        "path": "hero.png",
                        "content_hash": image["content_hash"],
                        "role": "primary",
                        "label": "Hero",
                    }
                ],
                "provenance": {"provider": "local", "model": "image-test"},
                "generation_parameters": {"width": 1024, "height": 1024},
            },
        )
        listed = service.invoke(
            "asset_sets.list",
            {"workspace_id": workspace_id, "version_id": version_id},
        )

        assert listed["items"] == [created]
        assert created["variants"][0]["media_type"] == "image/png"
        assert created["variants"][0]["content_hash"] == image["content_hash"]
        replayed = service.invoke(
            "asset_sets.create",
            {
                "workspace_id": workspace_id,
                "version_id": version_id,
                "idempotency_key": "asset-set:landing-hero",
                "kind": "image",
                "title": "Landing hero variants",
                "variants": [
                    {
                        "path": "hero.png",
                        "content_hash": image["content_hash"],
                        "role": "primary",
                        "label": "Hero",
                    }
                ],
                "provenance": {"provider": "local", "model": "image-test"},
                "generation_parameters": {"width": 1024, "height": 1024},
            },
        )
        assert replayed == created
        with pytest.raises(IdempotencyConflictError):
            service.invoke(
                "asset_sets.create",
                {
                    "workspace_id": workspace_id,
                    "version_id": version_id,
                    "idempotency_key": "asset-set:landing-hero",
                    "kind": "image",
                    "title": "Changed title",
                    "variants": [
                        {
                            "path": "hero.png",
                            "content_hash": image["content_hash"],
                            "role": "primary",
                            "label": "Hero",
                        }
                    ],
                    "provenance": {"provider": "local", "model": "image-test"},
                    "generation_parameters": {"width": 1024, "height": 1024},
                },
            )
        with pytest.raises(ValueError, match="prohibited key"):
            service.invoke(
                "asset_sets.create",
                {
                    "workspace_id": workspace_id,
                    "version_id": version_id,
                    "idempotency_key": "asset-set:unsafe",
                    "kind": "image",
                    "title": "Unsafe metadata",
                    "variants": [
                        {
                            "path": "hero.png",
                            "content_hash": image["content_hash"],
                            "role": "primary",
                            "label": "Hero",
                        }
                    ],
                    "provenance": {"api_key": "must-not-persist"},
                    "generation_parameters": {},
                },
            )
    finally:
        service.close()


def test_signed_renderer_pack_installs_atomically_and_rejects_traversal(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    valid = _renderer_bundle(tmp_path / "valid.frp", private_key, {"renderer.py": b"ok"})
    installer = RendererPackInstaller(tmp_path / "packs", trusted_keys={"official": public_key})

    installed = installer.verify_and_install(valid, user_confirmed=True)

    assert (installed.install_path / "renderer.py").read_bytes() == b"ok"
    unsafe = _renderer_bundle(
        tmp_path / "unsafe.frp",
        private_key,
        {"../outside.py": b"bad", "renderer.py": b"ok"},
        version="2",
    )
    with pytest.raises(RendererPackVerificationError, match="unsafe path"):
        installer.verify_and_install(unsafe, user_confirmed=True)
    assert not (tmp_path / "outside.py").exists()


def test_renderer_pack_requires_confirmation_and_rejects_tampered_signature(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    bundle = _renderer_bundle(tmp_path / "pack.frp", private_key, {"renderer.py": b"ok"})
    installer = RendererPackInstaller(tmp_path / "packs", trusted_keys={"official": public_key})
    with pytest.raises(PermissionError, match="confirmation"):
        installer.verify_and_install(bundle, user_confirmed=False)

    bundle = _renderer_bundle(
        tmp_path / "tampered.frp",
        Ed25519PrivateKey.generate(),
        {"renderer.py": b"ok"},
    )
    with pytest.raises(RendererPackVerificationError, match="signature"):
        installer.verify_and_install(bundle, user_confirmed=True)


def test_annotations_selections_and_edit_drafts_are_version_bound(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "notes.txt").write_text("alpha beta gamma", encoding="utf-8")
    service = build_local_service(tmp_path / "app")
    try:
        imported = service.invoke(
            "projects.import",
            {"name": "Collaboration", "residency": "local_only", "source_path": str(source)},
        )
        scope = {
            "workspace_id": imported["project"]["workspace_id"],
            "version_id": imported["project"]["active_version_id"],
            "path": "notes.txt",
        }
        presented = service.invoke("files.present", scope)
        identity = {
            "workspace_id": scope["workspace_id"],
            "version_id": scope["version_id"],
            "file_set_id": presented["job"]["file_set_id"],
            "source_hash": presented["job"]["source_hash"],
        }
        annotation = service.invoke(
            "annotations.update",
            {
                **identity,
                "expected_revision": 0,
                "annotations": [
                    {"id": "note-1", "body": "Review this", "locator": {"start": 0, "end": 5}}
                ],
            },
        )
        listed = service.invoke(
            "annotations.list",
            {key: identity[key] for key in ("workspace_id", "version_id", "file_set_id")},
        )
        selection = service.invoke(
            "selections.create",
            {
                **identity,
                "source_path": "notes.txt",
                "viewer_kind": "text",
                "locator_kind": "text_range",
                "locator": {"start": 0, "end": 5},
            },
        )
        recipe = service.invoke(
            "edit_recipes.create",
            {
                **identity,
                "kind": "text_patch",
                "operations": [{"operation": "replace", "start": 0, "end": 5, "text": "delta"}],
            },
        )
        discarded = service.invoke("edit_recipes.discard", {"recipe_id": recipe["id"]})

        assert annotation["revision"] == 1
        assert listed["document"] == annotation
        assert selection["source_hash"] == identity["source_hash"]
        assert discarded["status"] == "discarded"
        with pytest.raises(VersionConflictError, match="revision changed"):
            service.invoke(
                "annotations.update",
                {**identity, "expected_revision": 0, "annotations": []},
            )
        with pytest.raises(CommandRejectedError, match="trusted exporter"):
            service.invoke("edit_recipes.apply", {"recipe_id": recipe["id"]})
        with pytest.raises(PreviewScopeViolationError, match="source changed"):
            service.invoke(
                "selections.create",
                {
                    **identity,
                    "source_hash": "f" * 64,
                    "source_path": "notes.txt",
                    "viewer_kind": "text",
                    "locator_kind": "text_range",
                    "locator": {"start": 0, "end": 5},
                },
            )
        with pytest.raises(PreviewScopeViolationError, match="does not match"):
            service.invoke(
                "selections.create",
                {
                    **identity,
                    "source_path": "other.txt",
                    "viewer_kind": "text",
                    "locator_kind": "text_range",
                    "locator": {"start": 0, "end": 5},
                },
            )
        with pytest.raises(KeyError, match="FileSet not found"):
            service.invoke(
                "annotations.list",
                {
                    "workspace_id": identity["workspace_id"],
                    "version_id": identity["version_id"],
                    "file_set_id": str(uuid4()),
                },
            )
    finally:
        service.close()


def _renderer_bundle(
    target: Path,
    private_key: Ed25519PrivateKey,
    members: dict[str, bytes],
    *,
    version: str = "1",
) -> Path:
    payload_stream = io.BytesIO()
    with zipfile.ZipFile(payload_stream, "w") as payload:
        for name, content in members.items():
            payload.writestr(name, content)
    payload_bytes = payload_stream.getvalue()
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    manifest_bytes = json.dumps(
        {
            "entrypoint": "renderer.py",
            "features": ["pages"],
            "id": "test-pack",
            "input_media_types": ["application/test"],
            "license": "test-only",
            "limits": {"max_seconds": 30},
            "output_media_types": ["application/pdf"],
            "payload_sha256": payload_hash,
            "platform": "windows-x86_64",
            "reproducible": True,
            "sandbox": "native_restricted",
            "version": version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    metadata_bytes = json.dumps(
        {
            "key_id": "official",
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "payload_sha256": payload_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    with zipfile.ZipFile(target, "w") as bundle:
        bundle.writestr("metadata.json", metadata_bytes)
        bundle.writestr("metadata.sig", private_key.sign(metadata_bytes))
        bundle.writestr("manifest.json", manifest_bytes)
        bundle.writestr("manifest.sig", private_key.sign(manifest_bytes))
        bundle.writestr("payload.zip", payload_bytes)
    return target

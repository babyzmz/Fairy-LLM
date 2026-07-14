from __future__ import annotations

import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from fairy_core.transports.stdio import build_local_service
from fairy_core.workspace.file_sets import normalize_dependency_path


def test_file_probe_prefers_magic_and_reports_extension_conflict(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "misleading.txt").write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")
    service = build_local_service(tmp_path / "data")
    try:
        project = service.invoke(
            "projects.import",
            {"name": "Probe", "residency": "local_only", "source_path": str(source)},
        )
        descriptor = service.invoke(
            "files.probe",
            {
                "workspace_id": project["project"]["workspace_id"],
                "version_id": project["initial_version"]["id"],
                "path": "misleading.txt",
            },
        )

        assert descriptor["media_type"] == "image/png"
        assert descriptor["claimed_media_type"] == "text/plain"
        assert descriptor["media_type_conflict"] is True
    finally:
        service.close()


def test_gltf_file_set_is_deterministic_and_keeps_missing_and_blocked_members(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "models" / "textures").mkdir(parents=True)
    (source / "models" / "scene.gltf").write_text(
        json.dumps(
            {
                "asset": {"version": "2.0"},
                "buffers": [{"uri": "scene.bin"}],
                "images": [
                    {"uri": "textures/albedo.png"},
                    {"uri": "missing.png"},
                    {"uri": "https://example.com/tracker.png"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (source / "models" / "scene.bin").write_bytes(b"geometry")
    (source / "models" / "textures" / "albedo.png").write_bytes(b"\x89PNG\r\n\x1a\ntexture")
    service = build_local_service(tmp_path / "data")
    try:
        project = service.invoke(
            "projects.import",
            {"name": "glTF", "residency": "local_only", "source_path": str(source)},
        )
        request = {
            "workspace_id": project["project"]["workspace_id"],
            "version_id": project["initial_version"]["id"],
            "path": "models/scene.gltf",
        }
        first = service.invoke("file_sets.resolve", request)
        second = service.invoke("file_sets.resolve", request)
        fetched = service.invoke(
            "file_sets.get",
            {
                "workspace_id": request["workspace_id"],
                "version_id": request["version_id"],
                "file_set_id": first["id"],
            },
        )

        assert first == second == fetched
        assert first["kind"] == "gltf"
        assert [member["path"] for member in first["members"]] == [
            "models/scene.gltf",
            "models/scene.bin",
            "models/textures/albedo.png",
        ]
        assert first["missing_dependencies"] == ["models/missing.png"]
        assert first["blocked_dependencies"] == ["https://example.com/tracker.png"]
    finally:
        service.close()


def test_html_and_image_sequence_file_sets_never_escape_the_version(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "site" / "assets").mkdir(parents=True)
    (source / "site" / "index.html").write_text(
        '<img src="assets/hero.png"><script src="../../outside.js"></script>',
        encoding="utf-8",
    )
    (source / "site" / "assets" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\nhero")
    for frame in (1, 2, 3):
        (source / f"shot_{frame:04}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([frame]))
    service = build_local_service(tmp_path / "data")
    try:
        project = service.invoke(
            "projects.import",
            {"name": "Sets", "residency": "local_only", "source_path": str(source)},
        )
        scope = {
            "workspace_id": project["project"]["workspace_id"],
            "version_id": project["initial_version"]["id"],
        }
        html = service.invoke("file_sets.resolve", {**scope, "path": "site/index.html"})
        sequence = service.invoke("file_sets.resolve", {**scope, "path": "shot_0002.png"})

        assert [member["path"] for member in html["members"]] == [
            "site/index.html",
            "site/assets/hero.png",
        ]
        assert html["blocked_dependencies"] == ["../../outside.js"]
        assert sequence["kind"] == "image_sequence"
        assert [member["path"] for member in sequence["members"]] == [
            "shot_0002.png",
            "shot_0001.png",
            "shot_0003.png",
        ]
    finally:
        service.close()


def test_nested_obj_media_and_sequence_dependencies_use_their_declaring_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "assets" / "materials").mkdir(parents=True)
    (source / "assets" / "textures").mkdir()
    (source / "assets" / "model.obj").write_text(
        "mtllib materials/model.mtl\nv 0 0 0\n",
        encoding="utf-8",
    )
    (source / "assets" / "materials" / "model.mtl").write_text(
        "newmtl body\nmap_Kd ../textures/albedo.png\n",
        encoding="utf-8",
    )
    (source / "assets" / "textures" / "albedo.png").write_bytes(b"texture")
    (source / "clips").mkdir()
    (source / "clips" / "movie.mp4").write_bytes(b"video")
    (source / "clips" / "movie.vtt").write_text("WEBVTT\n", encoding="utf-8")
    (source / "frames").mkdir()
    for frame in (1, 2):
        (source / "frames" / f"shot_{frame:04}.png").write_bytes(bytes([frame]))
    service = build_local_service(tmp_path / "data")
    try:
        project = service.invoke(
            "projects.import",
            {"name": "Nested", "residency": "local_only", "source_path": str(source)},
        )
        scope = {
            "workspace_id": project["project"]["workspace_id"],
            "version_id": project["initial_version"]["id"],
        }
        obj = service.invoke("file_sets.resolve", {**scope, "path": "assets/model.obj"})
        media = service.invoke("file_sets.resolve", {**scope, "path": "clips/movie.mp4"})
        sequence = service.invoke(
            "file_sets.resolve",
            {**scope, "path": "frames/shot_0001.png"},
        )

        assert [member["path"] for member in obj["members"]] == [
            "assets/model.obj",
            "assets/materials/model.mtl",
            "assets/textures/albedo.png",
        ]
        assert [member["path"] for member in media["members"]] == [
            "clips/movie.mp4",
            "clips/movie.vtt",
        ]
        assert [member["path"] for member in sequence["members"]] == [
            "frames/shot_0001.png",
            "frames/shot_0002.png",
        ]
    finally:
        service.close()


_SEGMENT = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=1,
    max_size=12,
)


@given(st.lists(_SEGMENT, min_size=0, max_size=5), _SEGMENT)
def test_dependency_normalization_never_returns_an_escaped_path(
    directories: list[str],
    filename: str,
) -> None:
    primary = "/".join([*directories, "scene.gltf"])
    normalized = normalize_dependency_path(primary, f"textures/{filename}.png")

    assert normalized is not None
    assert not normalized.startswith("/")
    assert ":" not in normalized
    assert ".." not in normalized.split("/")
    assert normalize_dependency_path(primary, "https://example.com/a.bin") is None
    assert normalize_dependency_path(primary, "../" * (len(directories) + 2) + "escape") is None

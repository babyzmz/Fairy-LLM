from __future__ import annotations

import gzip
import io
import tarfile
import zipfile

import pytest

from fairy_core.presentation.document_inspector import DocumentInspectionError
from fairy_core.presentation.supplemental_inspector import SupplementalPackageInspector


def test_zip_inspection_lists_entries_without_extracting_them() -> None:
    content = _zip({"folder/readme.txt": b"Fairy", "image.png": b"png"})

    inspection = SupplementalPackageInspector().inspect(content, extension="zip")

    assert inspection.renderer == "builtin.archive"
    assert inspection.payload["kind"] == "archive"
    assert [entry["path"] for entry in inspection.payload["entries"]] == [
        "folder/readme.txt",
        "image.png",
    ]


def test_plain_gzip_is_a_bounded_single_member_container() -> None:
    content = gzip.compress(b"Fairy archive payload", mtime=0)

    inspection = SupplementalPackageInspector().inspect(content, extension="gz")

    assert inspection.payload["kind"] == "archive"
    assert inspection.payload["format"] == "gz"
    assert inspection.payload["entries"] == [
        {
            "path": "content",
            "size": len(b"Fairy archive payload"),
            "compressed_size": len(content),
            "kind": "file",
        }
    ]


def test_compressed_stream_rejects_an_unsafe_expansion_ratio() -> None:
    content = gzip.compress(b"0" * (1024 * 1024), mtime=0)

    with pytest.raises(DocumentInspectionError, match="ratio is unsafe"):
        SupplementalPackageInspector().inspect(content, extension="gz")


def test_epub_inspection_resolves_spine_to_inert_chapter_text() -> None:
    content = _zip(
        {
            "META-INF/container.xml": (
                b'<container><rootfiles><rootfile full-path="OEBPS/book.opf"/>'
                b"</rootfiles></container>"
            ),
            "OEBPS/book.opf": (
                b'<package><manifest><item id="chapter" href="chapter.xhtml"/>'
                b'</manifest><spine><itemref idref="chapter"/></spine></package>'
            ),
            "OEBPS/chapter.xhtml": b"<html><body><h1>Fairy</h1><p>Safe chapter</p></body></html>",
        }
    )

    inspection = SupplementalPackageInspector().inspect(content, extension="epub")

    assert inspection.renderer == "builtin.epub"
    assert inspection.payload["chapters"] == [
        {
            "path": "OEBPS/chapter.xhtml",
            "title": "chapter",
            "text": "Fairy Safe chapter",
        }
    ]


def test_mail_inspection_blocks_remote_content_and_hashes_attachments() -> None:
    content = (
        b"From: sender@example.test\r\nTo: user@example.test\r\nSubject: Review\r\n"
        b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=x\r\n\r\n"
        b"--x\r\nContent-Type: text/html\r\n\r\n<p>Hello</p><img src='https://tracker.test/p'>\r\n"
        b"--x\r\nContent-Type: text/plain\r\n"
        b"Content-Disposition: attachment; filename=note.txt\r\n\r\n"
        b"attachment\r\n--x--\r\n"
    )

    inspection = SupplementalPackageInspector().inspect(content, extension="eml")

    assert inspection.payload["kind"] == "mail"
    assert inspection.payload["remote_content_blocked"] is True
    assert "tracker.test" not in inspection.payload["body"]
    assert inspection.payload["attachments"][0]["filename"] == "note.txt"
    assert len(inspection.payload["attachments"][0]["sha256"]) == 64


def test_container_inspection_rejects_traversal_and_tar_links() -> None:
    with pytest.raises(DocumentInspectionError, match="unsafe path"):
        SupplementalPackageInspector().inspect(_zip({"../escape.txt": b"bad"}), extension="zip")

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "outside"
        archive.addfile(info)
    with pytest.raises(DocumentInspectionError, match="prohibited link"):
        SupplementalPackageInspector().inspect(stream.getvalue(), extension="tar")


def _zip(members: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return stream.getvalue()

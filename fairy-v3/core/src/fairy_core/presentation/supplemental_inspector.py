from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import lzma
import re
import stat
import tarfile
import zipfile
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import PurePosixPath
from xml.etree import ElementTree

from fairy_core.presentation.document_inspector import (
    DocumentInspection,
    DocumentInspectionError,
)
from fairy_core.presentation.models import PresentationFidelity

_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_EXPANDED_BYTES = 128 * 1024 * 1024
_MAX_MEMBER_BYTES = 32 * 1024 * 1024
_MAX_MEMBERS = 4096
_MAX_TEXT_CHARS = 500_000
_MAX_CHAPTERS = 200
_MAX_ATTACHMENTS = 500
_XML_UNSAFE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)


class SupplementalPackageInspector:
    """Build inert, bounded models for common container and message formats."""

    def inspect(self, content: bytes, *, extension: str) -> DocumentInspection:
        if len(content) > _MAX_SOURCE_BYTES:
            raise DocumentInspectionError("File exceeds the built-in presentation limit")
        extension = extension.lower()
        if extension == "epub":
            return self._epub(content)
        if extension == "eml":
            return self._mail(content)
        if extension == "zip":
            return self._zip(content)
        if extension in {"tar", "tgz"}:
            return self._tar(content, extension)
        if extension in {"gz", "bz2", "xz"}:
            return (
                self._tar(content, extension)
                if _is_tar(content)
                else self._compressed_stream(content, extension)
            )
        raise DocumentInspectionError("Format is not supported by the built-in inspector")

    def _zip(self, content: bytes) -> DocumentInspection:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = [_zip_entry(info) for info in _validate_zip(archive)]
        except zipfile.BadZipFile as error:
            raise DocumentInspectionError("Archive is invalid") from error
        return _archive_inspection("zip", entries)

    def _tar(self, content: bytes, extension: str) -> DocumentInspection:
        try:
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
                members = archive.getmembers()
                if len(members) > _MAX_MEMBERS:
                    raise DocumentInspectionError("Archive contains too many entries")
                expanded = 0
                entries = []
                for member in members:
                    path = _safe_path(member.name)
                    if member.issym() or member.islnk() or member.isdev():
                        raise DocumentInspectionError(
                            "Archive contains a prohibited link or device"
                        )
                    if member.size > _MAX_MEMBER_BYTES:
                        raise DocumentInspectionError("Archive entry exceeds the size limit")
                    expanded += member.size
                    if expanded > _MAX_EXPANDED_BYTES:
                        raise DocumentInspectionError("Archive expands beyond the size limit")
                    entries.append(
                        {
                            "path": path,
                            "size": member.size,
                            "kind": "directory" if member.isdir() else "file",
                        }
                    )
        except (tarfile.TarError, OSError) as error:
            raise DocumentInspectionError("Archive is invalid") from error
        return _archive_inspection(extension, entries)

    def _epub(self, content: bytes) -> DocumentInspection:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                infos = _validate_zip(archive)
                members = {info.filename.replace("\\", "/"): info for info in infos}
                container = _xml(archive, members, "META-INF/container.xml")
                rootfile = next(
                    (
                        node.attrib.get("full-path")
                        for node in container.iter()
                        if _local(node.tag) == "rootfile"
                    ),
                    None,
                )
                if not rootfile:
                    raise DocumentInspectionError("EPUB package has no root document")
                package_path = _safe_path(rootfile)
                package = _xml(archive, members, package_path)
                base = str(PurePosixPath(package_path).parent)
                manifest = {
                    node.attrib.get("id", ""): node.attrib.get("href", "")
                    for node in package.iter()
                    if _local(node.tag) == "item"
                }
                spine = [
                    node.attrib.get("idref", "")
                    for node in package.iter()
                    if _local(node.tag) == "itemref"
                ]
                chapters = []
                total_chars = 0
                for identifier in spine[:_MAX_CHAPTERS]:
                    href = manifest.get(identifier)
                    if not href or ":" in href or href.startswith(("/", "\\")):
                        continue
                    name = _safe_path(str(PurePosixPath(base, href)))
                    root = _xml(archive, members, name)
                    text = " ".join(part.strip() for part in root.itertext() if part.strip())
                    remaining = _MAX_TEXT_CHARS - total_chars
                    if remaining <= 0:
                        break
                    text = text[:remaining]
                    total_chars += len(text)
                    chapters.append({"path": name, "title": PurePosixPath(name).stem, "text": text})
        except zipfile.BadZipFile as error:
            raise DocumentInspectionError("EPUB package is invalid") from error
        return DocumentInspection(
            renderer="builtin.epub",
            fidelity=PresentationFidelity.NORMALIZED_HIGH,
            capabilities=("chapters", "search", "select", "copy", "annotate"),
            role="ebook-model",
            media_type="application/vnd.fairy.ebook+json",
            payload={"kind": "ebook", "chapters": chapters},
            partial=len(spine) > len(chapters) or total_chars >= _MAX_TEXT_CHARS,
        )

    def _mail(self, content: bytes) -> DocumentInspection:
        try:
            message = BytesParser(policy=policy.default).parsebytes(content)
        except (ValueError, TypeError) as error:
            raise DocumentInspectionError("Mail message is invalid") from error
        body = _mail_body(message)
        attachments = []
        for part in message.walk():
            filename = part.get_filename()
            if not filename:
                continue
            if len(attachments) >= _MAX_ATTACHMENTS:
                break
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                {
                    "filename": PurePosixPath(filename.replace("\\", "/")).name[:255],
                    "media_type": part.get_content_type(),
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        headers = {
            name.lower(): str(message.get(name, ""))[:4096]
            for name in ("From", "To", "Cc", "Subject", "Date", "Message-ID")
        }
        return DocumentInspection(
            renderer="builtin.mail",
            fidelity=PresentationFidelity.NORMALIZED_HIGH,
            capabilities=("headers", "search", "select", "copy", "attachments", "annotate"),
            role="mail-model",
            media_type="application/vnd.fairy.mail+json",
            payload={
                "kind": "mail",
                "headers": headers,
                "body": body[:_MAX_TEXT_CHARS],
                "attachments": attachments,
                "remote_content_blocked": True,
            },
            partial=len(body) > _MAX_TEXT_CHARS,
        )

    def _compressed_stream(self, content: bytes, extension: str) -> DocumentInspection:
        readers = {
            "gz": lambda: gzip.GzipFile(fileobj=io.BytesIO(content)),
            "bz2": lambda: bz2.BZ2File(io.BytesIO(content)),
            "xz": lambda: lzma.LZMAFile(io.BytesIO(content)),  # noqa: SIM115 - caller closes it
        }
        try:
            with readers[extension]() as stream:
                expanded = stream.read(_MAX_EXPANDED_BYTES + 1)
        except (EOFError, OSError, lzma.LZMAError) as error:
            raise DocumentInspectionError("Compressed stream is invalid") from error
        if len(expanded) > _MAX_EXPANDED_BYTES:
            raise DocumentInspectionError("Compressed stream expands beyond the size limit")
        if len(expanded) / max(len(content), 1) > 200:
            raise DocumentInspectionError("Compressed stream ratio is unsafe")
        return _archive_inspection(
            extension,
            [
                {
                    "path": "content",
                    "size": len(expanded),
                    "compressed_size": len(content),
                    "kind": "file",
                }
            ],
        )


def _validate_zip(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > _MAX_MEMBERS:
        raise DocumentInspectionError("Archive contains too many entries")
    expanded = 0
    for info in infos:
        _safe_path(info.filename.replace("\\", "/"))
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or info.flag_bits & 0x1:
            raise DocumentInspectionError("Archive contains a prohibited link or encrypted entry")
        if info.file_size > _MAX_MEMBER_BYTES:
            raise DocumentInspectionError("Archive entry exceeds the size limit")
        expanded += info.file_size
        if expanded > _MAX_EXPANDED_BYTES:
            raise DocumentInspectionError("Archive expands beyond the size limit")
        if info.compress_size > 0 and info.file_size / info.compress_size > 200:
            raise DocumentInspectionError("Archive compression ratio is unsafe")
    return infos


def _is_tar(content: bytes) -> bool:
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*"):
            return True
    except (tarfile.TarError, OSError):
        return False


def _zip_entry(info: zipfile.ZipInfo) -> dict[str, object]:
    return {
        "path": info.filename.replace("\\", "/"),
        "size": info.file_size,
        "compressed_size": info.compress_size,
        "kind": "directory" if info.is_dir() else "file",
    }


def _archive_inspection(format_name: str, entries: list[dict[str, object]]) -> DocumentInspection:
    return DocumentInspection(
        renderer="builtin.archive",
        fidelity=PresentationFidelity.NATIVE,
        capabilities=("entries", "search", "select", "copy", "annotate"),
        role="archive-model",
        media_type="application/vnd.fairy.archive+json",
        payload={"kind": "archive", "format": format_name, "entries": entries},
    )


def _xml(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    name: str,
) -> ElementTree.Element:
    info = members.get(name)
    if info is None:
        raise DocumentInspectionError(f"Package is missing {name}")
    content = archive.read(info)
    if _XML_UNSAFE.search(content):
        raise DocumentInspectionError("Package XML contains prohibited declarations")
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise DocumentInspectionError("Package XML is invalid") from error


def _mail_body(message: Message) -> str:
    plain = []
    html = []
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            value = part.get_content()
        except (LookupError, UnicodeError):
            continue
        if not isinstance(value, str):
            continue
        (plain if part.get_content_type() == "text/plain" else html).append(value)
    if plain:
        return "\n\n".join(plain)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", "\n".join(html))).strip()


def _safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in value
    ):
        raise DocumentInspectionError("Package contains an unsafe path")
    return path.as_posix()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


__all__ = ["SupplementalPackageInspector"]

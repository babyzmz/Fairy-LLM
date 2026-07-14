from __future__ import annotations

import base64
import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any
from uuid import UUID
from xml.etree import ElementTree

from fairy_core.domain.ids import new_id
from fairy_core.presentation.models import DerivedAsset, PresentationFidelity

_MAX_PACKAGE_BYTES = 64 * 1024 * 1024
_MAX_EXPANDED_BYTES = 128 * 1024 * 1024
_MAX_MEMBER_BYTES = 16 * 1024 * 1024
_MAX_MEMBERS = 4096
_MAX_TEXT_CHARS = 500_000
_MAX_ROWS = 2_000
_MAX_COLUMNS = 200
_MAX_EMBEDDED_PREVIEW = 4 * 1024 * 1024
_XML_UNSAFE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)


class DocumentInspectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DocumentInspection:
    renderer: str
    fidelity: PresentationFidelity
    capabilities: tuple[str, ...]
    role: str
    media_type: str
    payload: dict[str, Any]
    partial: bool = False

    def asset(self, presentation_id: UUID) -> DerivedAsset:
        encoded = _canonical_bytes(self.payload)
        return DerivedAsset(
            id=new_id(),
            presentation_id=presentation_id,
            role=self.role,
            media_type=self.media_type,
            content_hash=hashlib.sha256(encoded).hexdigest(),
            byte_length=len(encoded),
            storage_key=f"embedded:{hashlib.sha256(encoded).hexdigest()}",
            metadata=MappingProxyType({"payload": self.payload}),
        )


class DocumentPackageInspector:
    """Produces bounded, inert preview models from document packages."""

    def inspect(self, content: bytes, *, extension: str) -> DocumentInspection:
        if len(content) > _MAX_PACKAGE_BYTES:
            raise DocumentInspectionError("Document package exceeds the preview size limit")
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as error:
            raise DocumentInspectionError("Document package is invalid") from error
        with archive:
            members = self._validate(archive)
            extension = extension.lower()
            if extension == "docx":
                return self._docx(archive, members)
            if extension == "pptx":
                return self._pptx(archive, members)
            if extension == "xlsx":
                return self._xlsx(archive, members)
            if extension in {"odt", "odp", "ods"}:
                return self._odf(archive, members, extension)
            if extension in {"pages", "key", "numbers"}:
                return self._iwork(archive, members, extension)
        raise DocumentInspectionError("Document package is not supported by the built-in viewer")

    @staticmethod
    def _validate(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
        infos = archive.infolist()
        if len(infos) > _MAX_MEMBERS:
            raise DocumentInspectionError("Document package contains too many entries")
        expanded = 0
        members: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            name = info.filename.replace("\\", "/")
            path = PurePosixPath(name)
            mode = info.external_attr >> 16
            if (
                path.is_absolute()
                or any(part in {"", ".", ".."} for part in path.parts)
                or ":" in name
                or stat.S_ISLNK(mode)
                or info.flag_bits & 0x1
            ):
                raise DocumentInspectionError("Document package contains an unsafe entry")
            if info.file_size > _MAX_MEMBER_BYTES:
                raise DocumentInspectionError("Document package entry exceeds the size limit")
            expanded += info.file_size
            if expanded > _MAX_EXPANDED_BYTES:
                raise DocumentInspectionError("Document package expands beyond the size limit")
            if info.compress_size > 0 and info.file_size / info.compress_size > 200:
                raise DocumentInspectionError("Document package compression ratio is unsafe")
            members[name] = info
        return members

    def _docx(
        self, archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo]
    ) -> DocumentInspection:
        root = self._xml(archive, members, "word/document.xml")
        blocks: list[dict[str, Any]] = []
        for paragraph in root.iter():
            if _local(paragraph.tag) != "p":
                continue
            text = "".join(node.text or "" for node in paragraph.iter() if _local(node.tag) == "t")
            if text:
                blocks.append({"kind": "paragraph", "text": text})
            if sum(len(str(item.get("text", ""))) for item in blocks) >= _MAX_TEXT_CHARS:
                break
        return DocumentInspection(
            renderer="builtin.ooxml",
            fidelity=PresentationFidelity.CONTENT_ONLY,
            capabilities=("search", "select", "copy", "annotate"),
            role="document-model",
            media_type="application/vnd.fairy.document+json",
            payload={"kind": "document", "blocks": blocks},
        )

    def _pptx(
        self, archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo]
    ) -> DocumentInspection:
        names = sorted(
            (name for name in members if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=_natural_key,
        )
        slides = []
        for index, name in enumerate(names[:500], start=1):
            root = self._xml(archive, members, name)
            texts = [node.text or "" for node in root.iter() if _local(node.tag) == "t"]
            slides.append({"number": index, "text": "\n".join(filter(None, texts))})
        return DocumentInspection(
            renderer="builtin.ooxml",
            fidelity=PresentationFidelity.CONTENT_ONLY,
            capabilities=("slides", "search", "select", "copy", "annotate"),
            role="slide-model",
            media_type="application/vnd.fairy.slides+json",
            payload={"kind": "slides", "slides": slides},
        )

    def _xlsx(
        self, archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo]
    ) -> DocumentInspection:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in members:
            shared_root = self._xml(archive, members, "xl/sharedStrings.xml")
            for item in shared_root.iter():
                if _local(item.tag) == "si":
                    shared.append(
                        "".join(node.text or "" for node in item.iter() if _local(node.tag) == "t")
                    )
        sheets = []
        names = sorted(
            (name for name in members if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
            key=_natural_key,
        )
        for index, name in enumerate(names[:100], start=1):
            root = self._xml(archive, members, name)
            rows = []
            for row in (node for node in root.iter() if _local(node.tag) == "row"):
                values = []
                for cell in (node for node in row if _local(node.tag) == "c"):
                    kind = cell.attrib.get("t")
                    value_node = next((node for node in cell if _local(node.tag) == "v"), None)
                    value = "" if value_node is None else value_node.text or ""
                    if kind == "s" and value.isdigit() and int(value) < len(shared):
                        value = shared[int(value)]
                    values.append(value)
                    if len(values) >= _MAX_COLUMNS:
                        break
                rows.append(values)
                if len(rows) >= _MAX_ROWS:
                    break
            sheets.append({"name": f"Sheet {index}", "rows": rows})
        return DocumentInspection(
            renderer="builtin.ooxml",
            fidelity=PresentationFidelity.CONTENT_ONLY,
            capabilities=("sheets", "search", "select", "copy", "annotate"),
            role="workbook-model",
            media_type="application/vnd.fairy.workbook+json",
            payload={"kind": "workbook", "sheets": sheets},
            partial=any(len(sheet["rows"]) >= _MAX_ROWS for sheet in sheets),
        )

    def _odf(
        self,
        archive: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        extension: str,
    ) -> DocumentInspection:
        root = self._xml(archive, members, "content.xml")
        lines = []
        for node in root.iter():
            if _local(node.tag) in {"p", "h"}:
                text = "".join(node.itertext()).strip()
                if text:
                    lines.append(text)
            if sum(map(len, lines)) >= _MAX_TEXT_CHARS:
                break
        kind = {"odt": "document", "odp": "slides", "ods": "workbook"}[extension]
        return DocumentInspection(
            renderer="builtin.odf",
            fidelity=PresentationFidelity.CONTENT_ONLY,
            capabilities=("search", "select", "copy", "annotate"),
            role=f"{kind}-model",
            media_type=f"application/vnd.fairy.{kind}+json",
            payload={
                "kind": "document",
                "blocks": [{"kind": "paragraph", "text": line} for line in lines],
            },
        )

    def _iwork(
        self,
        archive: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        extension: str,
    ) -> DocumentInspection:
        candidates = (
            "QuickLook/Thumbnail.jpg",
            "QuickLook/Preview.jpg",
            "preview.jpg",
            "preview-web.jpg",
        )
        for name in candidates:
            info = members.get(name)
            if info is None or info.file_size > _MAX_EMBEDDED_PREVIEW:
                continue
            content = archive.read(info)
            return DocumentInspection(
                renderer="builtin.iwork-quicklook",
                fidelity=PresentationFidelity.APPROXIMATE,
                capabilities=("zoom", "pan", "annotate"),
                role="quicklook-preview",
                media_type="image/jpeg",
                payload={
                    "kind": "image",
                    "data_base64": base64.b64encode(content).decode("ascii"),
                    "source_format": extension,
                },
            )
        return DocumentInspection(
            renderer="builtin.iwork-package",
            fidelity=PresentationFidelity.CONTENT_ONLY,
            capabilities=("inspect", "annotate"),
            role="package-summary",
            media_type="application/vnd.fairy.package+json",
            payload={
                "kind": "package",
                "format": extension,
                "message": "This iWork file has no embedded Quick Look preview.",
            },
            partial=True,
        )

    @staticmethod
    def _xml(
        archive: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        name: str,
    ) -> ElementTree.Element:
        info = members.get(name)
        if info is None:
            raise DocumentInspectionError(f"Document package is missing {name}")
        content = archive.read(info)
        if _XML_UNSAFE.search(content):
            raise DocumentInspectionError("Document XML contains prohibited declarations")
        try:
            return ElementTree.fromstring(content)
        except ElementTree.ParseError as error:
            raise DocumentInspectionError("Document XML is invalid") from error


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value))


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


__all__ = ["DocumentInspectionError", "DocumentPackageInspector"]

# File Format And Renderer Pack Inventory

**Status:** Architecture baseline, 2026-07-14

This inventory is a release and licensing gate. A format is not supported until
its parser, converter, viewer, export path, license notice, sandbox profile, and
golden corpus are registered. “View” never implies pixel-identical authoring.

| Family | Representative inputs | Primary presentation | Initial fidelity | Implementation / license constraint |
| --- | --- | --- | --- | --- |
| Built-in text | TXT, source, Markdown, JSON, YAML, XML, CSV | sanitized text, syntax, bounded table | native | Fairy code; parser licenses recorded in lockfiles |
| Built-in document | PDF | PDF.js pages, text, outline, annotations | native | PDF.js Apache-2.0; active content disabled |
| Built-in web image | PNG, JPEG, GIF, WebP, AVIF, safe SVG | browser raster/vector | native | SVG sanitized; no script or external URI |
| Built-in media | browser-supported audio/video, VTT | media element, captions | native | browser codecs vary by device and must be probed |
| Built-in 3D | glTF, GLB | Three.js scene, hierarchy, material | native | Three.js MIT; external resources must resolve in FileSet |
| Office pack | DOC/X, XLS/X, PPT/X, ODT/S/P, RTF | PDF, semantic text/tables/slides | normalized_high | LibreOffice MPL-2.0; macros and links disabled |
| Diagram pack | VSD/X, PUB | PDF, page images, text | approximate | only filters verified in installed converter inventory |
| iWork package | Pages, Numbers, Keynote | embedded preview, package metadata | approximate/content_only | no bundled Apple converter on Windows |
| Professional image | TIFF, HEIF, camera RAW, PSD, EXR, HDR | tiled color-managed raster, metadata | normalized_high | codec/patent redistribution reviewed per pack |
| Media pack | broad FFmpeg demux/codec set, subtitles | proxy, waveform, keyframes, captions | normalized_high | FFmpeg build configuration and LGPL/GPL status fixed in manifest |
| Data pack | Parquet, Arrow, SQLite, Notebook | schema, bounded rows, cells, charts | normalized_high | DuckDB/Arrow licenses and extension loading policy recorded |
| CAD pack | STEP, IGES, BREP, STL, OBJ, PLY, DXF | glTF, SVG sheets, properties, measure | normalized_high/approximate | Open CASCADE LGPL exception; DXF feature matrix explicit |
| BIM pack | IFC, IFCZIP | glTF, spatial tree, properties | normalized_high | IfcOpenShell LGPL; IDS/BCF separate capabilities |
| DCC/3D pack | FBX, COLLADA, 3MF, USD, BLEND | glTF, scene tree, materials | normalized_high/approximate | Blender GPL is external process; FBX SDK redistribution reviewed |
| Archive pack | ZIP, 7z, TAR | bounded entry tree, safe extraction | native | decompression ratio and path policy mandatory |
| Ebook pack | EPUB | sanitized chapters, navigation, images | normalized_high | embedded HTML has no script/network |
| Mail pack | EML, MSG | headers, body, attachment tree | normalized_high/approximate | remote content and tracking disabled |
| Font pack | TTF, OTF, WOFF/2 | metadata, isolated glyph sheet | normalized_high | font embedding and malformed-table checks required |

## Deferred Specialist Families

GIS, DICOM, EDA, scientific volume/mesh, and discipline-specific proprietary
formats require separate semantic, safety, licensing, and validation ADRs. They
must not be routed through generic archive or 3D handling to imply support.

## Pack Release Record

Every pack release records exact upstream versions and source URLs, SPDX
identifiers, notices, build configuration, redistribution rights, optional
patent-sensitive codecs, signature certificate, payload digest, supported
platforms, sandbox profile, reproducibility result, and golden-corpus result.
Unknown or incompatible licensing blocks publication rather than silently
removing notices or downloading an unsigned binary at runtime.


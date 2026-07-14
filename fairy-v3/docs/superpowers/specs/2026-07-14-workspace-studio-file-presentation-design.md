# Workspace Studio File Presentation Design

**Status:** Approved on 2026-07-14

**Scope:** Fairy V3 managed Workspaces, file presentation, lightweight edits,
renderer packs, generated assets, and selection references

## Product Boundary

Workspace Studio is the common file surface for scratch Conversations and
Projects. It previews, compares, annotates, and applies bounded non-destructive
edits to managed files. It is not an Office, CAD, DCC, or media-authoring suite.
Runtime Preview remains a separate execution concern and may be pinned beside a
file presentation.

All source files belong to an immutable Workspace Version. Presentation never
mutates that Version. An accepted edit recipe creates a new object, file entry,
and candidate Version through the Command Bus. Project candidates require
Review; validated scratch Conversation candidates may follow their existing
automatic-promotion policy.

## Storage Architecture

`WorkspaceObjectStore` stores binary and large source objects by SHA-256.
Small text files remain in the internal Git snapshot. A Version manifest binds
each path to media type, byte size, digest, storage class, and optional object
reference. Object publication is atomic: bytes are streamed to a temporary
object, size and digest are verified, and only then is the immutable object
made addressable.

Default limits are 4 GiB per source file, 50 GiB per local Project, and a 20
GiB derivative cache. Conversion pauses before local free space falls below 10
GiB. Limits are device preferences with bounded administrator policy. Existing
Changeset limits continue to govern model-authored text changes; `AssetMutation`
is the streaming path for binary imports, generated media, and converter output.

Binary reads never cross JSON-RPC as whole-file Base64 values. The Rust host
issues short-lived, single-purpose loopback read sessions with HTTP Range
support. Core RPC may return UTF-8 text only when the verified object is below
2 MiB. Read sessions bind tenant, Workspace, Version, path, digest, allowed byte
range, expiry, and one random bearer token whose hash alone is retained.

## Logical Files

A `FileSet` describes one logical asset with a primary entry and immutable
dependency closure. Examples include glTF with buffers and textures, OBJ with
MTL files, video with subtitles, image sequences, CAD assemblies, iWork
packages, and HTML sites. Its manifest records member paths and hashes, parser
version, missing dependencies, and deterministic ordering. Resolution rejects
traversal, links outside the Version, ambiguous case-folded paths, and mutable
external URIs.

## Presentation Domain

File presentation is independent from `PreviewSession` and `RuntimeGraph`.
The domain owns:

- `FilePresentation`: one source Version, capability result, fidelity, current
  derivative set, and viewer state;
- `FileRenderJob`: a durable, cancellable conversion attempt;
- `DerivedAsset`: immutable PDF, image pyramid, proxy media, waveform, glTF,
  SVG, property tree, text, or metadata output;
- `AnnotationDocument`: revision-fenced user annotations anchored to source
  coordinates;
- `EditRecipe`: a bounded, replayable non-destructive transformation;
- `SelectionReference`: an immutable source hash plus typed locator suitable
  for attachment to a Conversation Turn.

Render jobs transition through `probing`, `waiting_for_pack`, `queued`,
`converting`, and `validating`, then exactly one of `ready`, `partial`,
`failed`, `canceled`, or `quarantined`. A cache key includes every source and
dependency digest, renderer pack and parser version, normalized parameters,
platform, and color configuration. A cached derivative is used only after its
manifest and object hashes validate.

Fidelity is explicit: `native`, `normalized_high`, `approximate`,
`content_only`, or `unsupported`. Degradation is never hidden behind a generic
success state.

## Renderer Packs

Heavy converters ship as signed, on-demand renderer packs rather than Core or
desktop dependencies. A manifest declares pack identity and version, platform,
input formats, derivative types, features, limits, license notices, sandbox
profile, reproducibility class, payload SHA-256, and signer identity.

Install and update require an explicit user action. The Pack Manager verifies
repository metadata, manifest signature, payload digest, and Windows
Authenticode where applicable before atomic activation. Existing jobs retain
their exact pack version; removal is blocked while a durable job or retained
derivative manifest references that version.

The Broker starts converters with a restricted token, Job Object limits,
private temporary directory, empty ambient environment, no network, and no
Workspace write access. Inputs and outputs are explicit handles. Linux-native
converters may use the attested FairySandbox. Converter stdout, stderr, and
documents are untrusted and never become commands or user-visible raw logs.

Initial pack families are Office, Media, Professional Image, Data, CAD, BIM,
DCC/3D, Archive/Ebook/Mail, and Font. Proprietary support requires a distributable
SDK or a separately installed, authorized user component.

## Viewer And Editing Surface

Workspace Studio has a virtualized file tree and FileSet/queue navigation on
the left, tabbed viewers in the center, and properties, layers, object trees,
annotations, fidelity, and edit drafts on the right. Runtime web Preview is a
pinnable center tab. At most two heavyweight viewers remain active; inactive
viewers release GPU, decoder, file, and read-session resources.

Light edits are recipes, not in-place writes. Supported classes include text
and table patches, Office/PDF annotations and ordering, image crop/rotate/resize
and basic color adjustment, media trim/volume/poster/subtitle timing, and
CAD/3D measurement, section, visibility, transform, material, camera, and
annotations. Apply validates the draft revision and export capability before
creating a new source object and Version. Discard removes only the draft.

## Format Strategy

Built-in viewers handle text, code, Markdown, JSON, YAML, XML, CSV, PDF, safe
web images, sanitized SVG, browser-supported media, and glTF. Packs normalize
Office and ODF to PDF plus semantic structure; professional images to tiled
color-managed rasters; media through FFmpeg proxies; CAD/BIM and DCC assets to
glTF, vector sheets, object trees, and metadata; and data/container formats to
bounded tabular or extracted presentations.

iWork on Windows uses package structure and embedded previews and is therefore
normally `approximate` or `content_only`. Higher fidelity requires explicit
one-time cloud consent or a future macOS worker. GIS, DICOM, EDA, and specialist
scientific formats are deferred until their semantic and regulatory contracts
are designed.

## Chat, Generation, And Sync

A `SelectionReference` stores the Workspace, Version, path/FileSet, source
digest, viewer kind, and one typed locator: text range, PDF region, sheet range,
time range, image region, scene node, CAD entity, or archive entry. The Core
revalidates the source and locator before a model can consume extracted content.

Generated image, audio, video, and later 3D outputs form an `AssetSet` with
variants, provenance, generation parameters safe for display, and apply or
regenerate actions. Sync transfers source manifests and required immutable
objects, annotations, and edit recipes. Device-specific derivatives are
regenerated and are not authoritative sync state.

Cloud conversion requires a scoped, expiring `CloudConversionGrant` naming the
source objects, converter class, destination region, retention policy, and
purpose. Local-first behavior and refusal remain available without feature
breakage beyond the unavailable conversion.

## Public Contract

Models: `FileDescriptorModel`, `FileSetModel`, `FileCapabilityModel`,
`RendererPackModel`, `FileRenderJobModel`, `FilePresentationModel`,
`DerivedAssetModel`, `AnnotationDocumentModel`, `EditRecipeModel`,
`SelectionReferenceModel`, `AssetSetModel`, and `FileReadSessionModel`.

Methods: `files.probe`, `files.present`, `files.cancel`, `files.open_stream`,
`files.compare`, `file_sets.get`, `file_sets.resolve`, `annotations.list`,
`annotations.update`, `edit_recipes.create`, `edit_recipes.update`,
`edit_recipes.apply`, `edit_recipes.discard`, `renderer_packs.list`,
`renderer_packs.install`, `renderer_packs.update`, `renderer_packs.remove`,
`renderer_packs.health`, and `selections.create`.

Errors: `FORMAT_UNSUPPORTED`, `PACK_REQUIRED`, `PACK_UNTRUSTED`,
`FILE_TOO_LARGE`, `FILE_ENCRYPTED`, `ACTIVE_CONTENT_BLOCKED`,
`DEPENDENCY_MISSING`, `CONVERSION_TIMEOUT`, `DERIVATIVE_INVALID`,
`FIDELITY_DEGRADED`, `CACHE_QUOTA_EXCEEDED`, and `EDIT_NOT_EXPORTABLE`.

The Ledger stores job state, bounded public summaries, digests, and typed error
codes. It never stores source content, passwords, cloud grant secrets, raw
converter logs, or binary derivatives.

## Delivery Sequence

1. architecture, threat, format, and license contracts;
2. object store, streaming asset mutation, range sessions, and quotas;
3. file catalog and FileSet dependency closure;
4. presentation jobs, cache, Pack Manager, and Broker;
5. Workspace Studio shell, basic viewers, annotations, and selections;
6. PDF, Office, iWork, and Data packs;
7. image, media, and generated AssetSets;
8. CAD, BIM, and 3D packs;
9. archives, ebooks, mail, fonts, compare, and sync; and
10. adversarial security, performance, installation, and release closure.

## Acceptance Criteria

1. Every displayed byte resolves to one immutable Workspace Version and digest.
2. Large files stream with bounded memory and revocable Range sessions.
3. A converter cannot access the network, host credentials, or unrelated files.
4. Fidelity, missing dependencies, and unsupported edits are visible and typed.
5. Applying an edit always creates a new object and Version.
6. Selection references survive refresh and reject stale or changed sources.
7. Runtime Preview and file presentation coexist without sharing authority.
8. Built-in operation remains useful while every optional pack is absent.


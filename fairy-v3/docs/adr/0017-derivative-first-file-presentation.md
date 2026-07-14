# ADR 0017: Derivative-First File Presentation

## Decision

Fairy introduces Workspace Studio as a file-presentation domain independent of
Runtime Preview. It uses immutable source objects and signed, sandboxed renderer
packs to produce validated derivatives. Direct browser viewing remains a fast
path for safe formats; external applications are an explicit fallback, not the
primary architecture.

Large and binary files use a content-addressed object store and bounded Range
read sessions. Multi-file formats are represented by immutable `FileSet`
manifests. Viewer annotations, selections, and edit recipes bind exact source
digests. Applying a light edit creates a new source object and Workspace Version
and never overwrites an existing Version.

## Rationale

Embedding every native editor would expand Fairy's trusted computing base,
installer size, memory use, and licensing exposure while still yielding
inconsistent behavior across machines. Derivatives provide a stable local-first
contract for Office, media, CAD/BIM, DCC, data, and container formats without
granting untrusted documents execution authority.

Tiered fidelity is part of the public result. A normalized or approximate
presentation cannot claim native fidelity. iWork high-fidelity conversion and
other cloud processing require a one-time, scoped grant and are never an
automatic fallback.

## Consequences

Fairy must operate a signed pack repository, versioned converter sandbox,
derivative cache, legal inventory, and representative golden corpus. Conversion
is asynchronous and cancellable. Devices synchronize source authority and edit
metadata, while regenerating non-authoritative derivatives locally.

Runtime Preview continues to execute only validated runnable Workspace Versions.
File presentation cannot start a project process, and Runtime Preview cannot
be used as a general document renderer.


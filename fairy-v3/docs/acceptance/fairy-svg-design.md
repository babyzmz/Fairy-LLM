# Fairy SVG form integration decision

Status: implemented as a source migration; Windows integration verification pending.

## Boundaries

The shared SVG runtime is independent from Agent, TTS generation, model routing and
native window operations. Chat and pet are separate hosts. React controls mount and
update; one instance-owned monotonic active-time RAF controls animation. Every SVG
resource ID is local to the mount. Checked-in templates are the only markup source.

Chat uses current conversation/turn facts and a read-only actual-playback selector;
preparing, ambient and unrelated turns do not become chat speech. UI status and
permission prompts remain authoritative, not a decorative expression. The existing
global amplitude transport is unchanged and needs parallel-source native testing.

## Settings compatibility

Desktop preferences keep persistent schema 11. PetForm defaults to liquid_glass,
and chat_fairy_eye_enabled defaults to true. Old JSON records deserialize through
serde defaults. Old schema-11 binaries ignore unknown fields; rolling back source
therefore does not require a destructive preference reset. Unknown PetForm values
are rejected rather than silently erasing settings.

Bounded render messages emit schema 5 with form. Legacy schema 4 is accepted and
normalized to liquid_glass; fields outside the bounded schema remain rejected.
This distinguishes additive persistent storage from the render transport contract.

## Native lifecycle

HDD Eye disallows the native startup/rebind configuration in Rust. The render app
gates its start request, snapshot updates and drag/display lifecycle callbacks by
form, and rejects late health completions from another form epoch. SVG waits while
native state is starting/running/stopping. The existing native host serializes stop;
its low-level implementation is not rewritten in this migration.

The SVG form uses the existing physical anchor/DPI projection and input hit target.
Only its compact input receives an opaque backing because the SVG path does not
render a liquid capsule. Liquid-form input styling and saved glass settings remain.

## Architecture decision review

This remains a procedural vector form without imported avatar image textures. It
adds a supported alternative visual path; it does not reinterpret existing liquid
optics as simulated glass. Before merging, review the repository's full current ADR
requirements and the native acceptance matrix. This note does not claim approval
of unseen ADR clauses or a completed native release gate.

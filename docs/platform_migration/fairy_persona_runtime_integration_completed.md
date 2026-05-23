# Fairy Persona Runtime Integration Completed

## Status

Closed and regression-checked.

## What is now authoritative

- Backend runtime state is the source of truth.
- The frontend Fairy avatar consumes backend `runtime_state` first.
- Frontend heuristic presence logic is now fallback-only.

## Bound runtime states

- `booting`
- `warming_up`
- `idle`
- `thinking`
- `analyzing`
- `replying`
- `error`
- `sleeping`

## Runtime outputs now wired

- `/system/state`
- `message_start.meta.runtime_state`
- `message_end.meta.runtime_state`

## Persona bindings

- Fairy avatar mode follows backend runtime state.
- Runtime voice lines are driven by meaningful state transitions and timeline events.
- Voice playback is guarded by queueing, cooldowns, deduplication, and transition whitelisting.

## Current policy

- Backend-first state consumption
- Frontend fallback inference only when backend runtime state is not yet available
- No planner, provider, or semantic routing changes as part of persona integration

## Regression closeout

- Startup lifecycle verified
- Simple request lifecycle verified
- Controlled orchestration lifecycle verified
- Error and recovery lifecycle verified
- Stopped/restart lifecycle verified

## Remaining note

Long external-search scenarios can still vary by environment and network conditions. Persona state binding is validated independently from provider volatility.

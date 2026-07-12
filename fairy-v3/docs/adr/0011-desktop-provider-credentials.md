# ADR 0011: Keep Desktop Provider Credentials in the Host

- Status: Accepted
- Date: 2026-07-12

## Context

Provider profiles were previously supplied only through process environment
variables. That supported controlled development runs but left a first-run user
unable to configure a model from the desktop. Persisting API keys in Core state,
the Ledger, browser storage, or ordinary JSON would violate the secret-egress
boundary.

## Decision

The Tauri main-window host owns desktop provider credential management. On
Windows, OpenRouter keys are encrypted with DPAPI for the current user and
written atomically below the application data directory. The adjacent provider
configuration contains only the selected public model ID. Secrets are never
returned to the Renderer, logged, written to SQLite, or included in a Core RPC.

After a configuration change, Tauri stops the existing Core, constructs a new
filtered LaunchSpec containing the decrypted key and public profile, and accepts
the operation only after the replacement Core passes its protocol handshake.
The Renderer then invalidates Provider and health queries. Removing the provider
deletes both files and performs the same supervised restart.

Cloud deployments continue to resolve credential references from their
deployment secret store. The DPAPI adapter is a Windows desktop adapter, not a
domain persistence mechanism.

## Consequences

- First-run desktop setup is usable without a shell or environment variables.
- Copying the encrypted credential file to another Windows user does not reveal
  or activate the key.
- Provider configuration is unavailable on unsupported hosts unless another
  secure credential adapter is added.
- A completed assistant turn suppresses its transient stream projection once
  the durable assistant message is visible.

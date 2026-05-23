# Runtime Architecture Note

The old dual-path "legacy vs new" migration is no longer the active design.

Canonical execution model:

- Deterministic direct path:
  - weather
  - time
  - location / map
  - system actions
- Bundle runtime path:
  - web-research
  - screen-understanding
  - news-intelligence
  - document-editing
  - terminal-agent

Canonical bundle definitions live under:

- `app/skills/bundles/<bundle_dir>/`

Each active bundle should define:

- `SKILL.md`
- `tools.json`
- `runtime.py`

Support code lives under:

- `app/tools/*`
- `app/web_access/*`
- `app/capabilities/*`

Deprecated concepts that should not be reintroduced on the main chat path:

- root-level `skills.*` imports
- legacy skill router / dispatcher layers
- invocation service / capability registry chat execution
- runtime-direct web / screen side paths

If a future cleanup removes the remaining compatibility names in runtime internals,
update this note together with the code.

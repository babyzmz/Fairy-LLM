# Contracts

This directory owns generated transport contracts. `openapi.json` is emitted
from the FastAPI adapter whose operation IDs are the public Core JSON-RPC
method names. Local JSON-RPC and cloud REST/SSE contract tests require complete
operation-ID coverage.

Regenerate the OpenAPI snapshot and desktop types from `fairy-v3/`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/generate-contracts.ps1
```

The generator is isolated in `tools/contracts/` with TypeScript 5.9.3 because
`openapi-typescript` 7.13.0 does not declare compatibility with the desktop's
TypeScript 7 toolchain. The generated declaration is consumed by TypeScript 7
at `desktop/src/core/generated/api.d.ts`.

Generated artifacts must not be edited by hand. `CoreMethodMap` contains a
type-level coverage assertion against generated OpenAPI operation IDs, so a
new or removed Core method fails the desktop build until every transport is
updated.

# Contracts

This directory owns generated transport contracts. OpenAPI and JSON Schema
are emitted by Core, then used to generate TypeScript and Rust models. Generated
artifacts must not be edited by hand, and local JSON-RPC plus cloud REST/SSE
must pass the same contract fixtures.

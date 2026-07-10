# ADR 0002: Use SeaweedFS for local S3 development

- Status: accepted
- Date: 2026-07-10

## Context

Fairy requires a provider-neutral S3-compatible object port for immutable
version snapshots, artifacts, logs, and preview captures. The original design
did not mandate a development implementation. MinIO Community Edition stopped
publishing maintained binaries and its repository was archived in April 2026,
so adopting it for a new 2026 stack would create an immediate maintenance and
security liability.

## Decision

Use SeaweedFS 4.39 in Docker Compose through its single-node `weed mini` S3
mode. Keep all application code on the AWS S3 API via boto3 and path-style
addressing. Production remains provider-neutral and may use AWS S3, a managed
compatible service, or a separately operated S3 implementation.

Version snapshots use `If-None-Match: *`, SHA-256 object metadata, scoped keys,
and a `HEAD` verification before a manifest can participate in Active Version
promotion.

## Consequences

- Development retains a small one-container S3 service with a pre-created
  bucket.
- SeaweedFS-specific APIs are forbidden in Fairy application code.
- S3 conditional-write behavior is covered by a real integration test.
- Production provider selection still requires operational and compliance
  review.

## References

- [SeaweedFS quick start](https://github.com/seaweedfs/seaweedfs#quick-start)
- [MinIO source-only notice](https://github.com/minio/minio#source-only-distribution)

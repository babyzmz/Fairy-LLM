# Legacy Baseline

Recorded before Fairy V3 implementation on 2026-07-10.

- fairy-desktop npm build: passed;
- fairy-desktop cargo check: passed;
- Python pytest: 342 passed, 17 failed, 3 subtests passed.

The Python failures predate V3 and are concentrated in a removed realtime
bundle, old ChatWorker constructor expectations, timezone runtime setup, and
existing web-access expectations. They are reference behavior only and are not
part of the Fairy V3 test gate.

V3 tests run from fairy-v3 and must remain independent from legacy modules.

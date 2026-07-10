# Fairy Desktop

The desktop package contains the React renderer, the Tauri host, the local
Core bridge, and the Rust local worker. The renderer has one privileged path:
the `core_rpc` Tauri command. It cannot invoke the worker or host processes
directly.

```powershell
npm install
npm test
npm run e2e
npm run build
npm run tauri -- build --debug --no-bundle
npm run tauri -- dev
```

The development Tauri process launches Python Core over line-oriented
JSON-RPC. Core then launches the same `fairy.exe` with `--local-worker` for
scoped Git workspace operations. Neither boundary invokes a host shell.

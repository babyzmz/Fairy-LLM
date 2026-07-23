import { createServer } from "vite";

const server = await createServer({
  server: {
    host: "127.0.0.1",
    port: 1431,
    strictPort: true,
  },
});

await server.listen();

let closing = false;
async function close() {
  if (closing) return;
  closing = true;
  await server.close();
  process.exitCode = 0;
}

process.once("SIGINT", close);
process.once("SIGTERM", close);
process.once("SIGHUP", close);

const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
function get(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => data += chunk);
      res.on('end', () => resolve({ status: res.statusCode, data }));
    }).on('error', reject);
  });
}
(async () => {
  const exe = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  const port = 9452;
  const profile = path.join(os.tmpdir(), 'fairy_edge_node_' + Date.now());
  fs.mkdirSync(profile, { recursive: true });
  const proc = spawn(exe, ['--headless', '--disable-gpu', '--no-sandbox', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
  try {
    let version = null;
    for (let i = 0; i < 10; i++) {
      try {
        const resp = await get(`http://127.0.0.1:${port}/json/version`);
        version = JSON.parse(resp.data);
        break;
      } catch {}
      await sleep(1000);
    }
    if (!version) throw new Error('cdp_version_unavailable');
    const list = JSON.parse((await get(`http://127.0.0.1:${port}/json/list`)).data);
    const page = list.find(p => p.type === 'page') || list[0];
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    let id = 0;
    const pending = new Map();
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    };
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    const send = (method, params={}) => new Promise((resolve) => { const msgId = ++id; pending.set(msgId, resolve); ws.send(JSON.stringify({ id: msgId, method, params })); });
    await send('Runtime.enable');
    await send('Page.enable');
    await send('Page.navigate', { url: 'data:text/html,<html><body><h1>Hello Node CDP</h1><a id="go" href="#">Go</a></body></html>' });
    await sleep(1000);
    const textResp = await send('Runtime.evaluate', { expression: 'document.body.innerText', returnByValue: true });
    const clickResp = await send('Runtime.evaluate', { expression: "document.querySelector('#go').click(); 'clicked'", returnByValue: true });
    console.log(JSON.stringify({ ok: true, browser: version.Browser, text: textResp.result.result.value, click: clickResp.result.result.value }));
    ws.close();
  } catch (err) {
    console.error(JSON.stringify({ ok: false, error: String(err && err.stack || err), exitCode: proc.exitCode }));
    process.exitCode = 1;
  } finally {
    try { proc.kill(); } catch {}
  }
})();

const { chromium } = require('D:/桌面/~/deskllmchat/.browser-runtime/node_modules/playwright-core');
(async()=>{
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', headless: true });
  const page = await browser.newPage();
  await page.goto('data:text/html,<html><body><h1>Hello Fairy</h1><a id="go" href="#">Go</a></body></html>', { waitUntil: 'domcontentloaded', timeout: 10000 });
  const text = (await page.textContent('body')).trim();
  await page.click('#go', { timeout: 5000 });
  console.log(JSON.stringify({ ok: true, text }));
  await browser.close();
})().catch(err => {
  console.error(JSON.stringify({ ok: false, error: String(err && err.stack || err) }));
  process.exit(1);
});

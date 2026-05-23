from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import websockets


SCREENSHOT_DIR = Path("data/screenshots")


@dataclass(slots=True)
class CdpTarget:
    target_id: str
    ws_url: str
    url: str
    title: str
    target_type: str


class CdpAttachBackend:
    def __init__(self, http_origin: str) -> None:
        self.http_origin = http_origin.rstrip("/")
        self._sessions: dict[str, CdpTarget] = {}

    @staticmethod
    def _set_windows_loop_policy() -> None:
        if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    def version(self) -> dict[str, Any]:
        with urlopen(f"{self.http_origin}/json/version", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def list_targets(self) -> list[dict[str, Any]]:
        with urlopen(f"{self.http_origin}/json/list", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def page_target(self) -> CdpTarget:
        for item in self.list_targets():
            if str(item.get("type") or "").strip().lower() == "page":
                return CdpTarget(
                    target_id=str(item.get("id") or "").strip(),
                    ws_url=str(item.get("webSocketDebuggerUrl") or "").strip(),
                    url=str(item.get("url") or "").strip(),
                    title=str(item.get("title") or "").strip(),
                    target_type="page",
                )
        raise RuntimeError("cdp_page_target_missing")

    def browser_ws_url(self) -> str:
        return str(self.version().get("webSocketDebuggerUrl") or "").strip()

    def _browser_send(self, commands: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        ws_url = self.browser_ws_url()
        if not ws_url:
            raise RuntimeError("cdp_browser_ws_url_missing")
        return self._send(ws_url, commands)

    def _target_by_id(self, target_id: str) -> CdpTarget:
        wanted = str(target_id or "").strip()
        for item in self.list_targets():
            item_id = str(item.get("id") or "").strip()
            if item_id != wanted:
                continue
            return CdpTarget(
                target_id=item_id,
                ws_url=str(item.get("webSocketDebuggerUrl") or "").strip(),
                url=str(item.get("url") or "").strip(),
                title=str(item.get("title") or "").strip(),
                target_type=str(item.get("type") or "").strip() or "page",
            )
        raise RuntimeError("cdp_target_not_found")

    def _create_target(self, url: str) -> CdpTarget:
        response = self._browser_send([("Target.createTarget", {"url": url or "about:blank"})])
        target_id = str(response[0].get("result", {}).get("targetId") or "").strip()
        if not target_id:
            raise RuntimeError("cdp_target_create_failed")
        for _ in range(10):
            try:
                target = self._target_by_id(target_id)
            except RuntimeError:
                time.sleep(0.1)
                continue
            if target.ws_url:
                return target
            time.sleep(0.1)
        raise RuntimeError("cdp_target_ws_missing")

    def _close_target(self, target_id: str) -> None:
        wanted = str(target_id or "").strip()
        if not wanted:
            return
        try:
            self._browser_send([("Target.closeTarget", {"targetId": wanted})])
        except Exception:
            return

    async def _send_async(self, ws_url: str, commands: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        async with websockets.connect(
            ws_url,
            compression=None,
            ping_interval=None,
            max_size=None,
        ) as ws:
            results: list[dict[str, Any]] = []
            next_id = 1
            for method, params in commands:
                payload = {"id": next_id, "method": method, "params": params or {}}
                await ws.send(json.dumps(payload))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    if data.get("id") == next_id:
                        results.append(data)
                        break
                next_id += 1
            return results

    def _send(self, ws_url: str, commands: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        self._set_windows_loop_policy()
        return asyncio.run(self._send_async(ws_url, commands))

    def smoke(self, smoke_html: str) -> dict[str, Any]:
        page = self.page_target()
        results = self._send(
            page.ws_url,
            [
                ("Runtime.enable", {}),
                ("Page.enable", {}),
                ("Page.navigate", {"url": smoke_html}),
            ],
        )
        time.sleep(0.8)
        results.extend(
            self._send(
                page.ws_url,
                [
                    ("Runtime.evaluate", {"expression": "document.body.innerText", "returnByValue": True}),
                    ("Runtime.evaluate", {"expression": "document.querySelector('#go').click(); 'clicked'", "returnByValue": True}),
                ],
            )
        )
        text_value = results[3].get("result", {}).get("result", {}).get("value", "")
        click_value = results[4].get("result", {}).get("result", {}).get("value", "")
        return {
            "status": "full" if str(click_value).strip() == "clicked" else "partial",
            "reason": "browser_automation_ready" if str(click_value).strip() == "clicked" else "page_operation_failed",
            "launch": "ok",
            "open_page": "ok",
            "extract": "ok" if str(text_value).strip() else "empty",
            "interaction_smoke": "ok" if str(click_value).strip() == "clicked" else "failed",
            "last_smoke_result": str(text_value).strip()[:120],
            "backend": "cdp_attach",
            "browser_ws_url": self.version().get("webSocketDebuggerUrl", ""),
        }

    def open_page(self, url: str, *, timeout_ms: int, wait_after_load_ms: int, capture_screenshot: bool) -> dict[str, Any]:
        page = self._create_target("about:blank")
        try:
            self._send(page.ws_url, [("Page.enable", {}), ("Runtime.enable", {}), ("Page.navigate", {"url": url})])
            time.sleep(max(0.2, wait_after_load_ms / 1000.0))
            return self.extract_target(page, requested_url=url, capture_screenshot=capture_screenshot)
        finally:
            self._close_target(page.target_id)

    def open_interactive_page(self, url: str, *, timeout_ms: int, wait_after_load_ms: int, capture_screenshot: bool) -> dict[str, Any]:
        page = self._create_target("about:blank")
        self._send(page.ws_url, [("Page.enable", {}), ("Runtime.enable", {}), ("Page.navigate", {"url": url})])
        time.sleep(max(0.2, wait_after_load_ms / 1000.0))
        handle_id = uuid.uuid4().hex[:12]
        self._sessions[handle_id] = page
        return self.extract_target(page, requested_url=url, capture_screenshot=capture_screenshot, handle_id=handle_id)

    def extract_session(self, handle_id: str, *, capture_screenshot: bool) -> dict[str, Any]:
        target = self._sessions.get(handle_id)
        if target is None:
            raise KeyError(f"Browser handle not found: {handle_id}")
        return self.extract_target(target, requested_url=target.url or "about:blank", capture_screenshot=capture_screenshot, handle_id=handle_id)

    def close_session(self, handle_id: str) -> None:
        target = self._sessions.pop(handle_id, None)
        if target is not None:
            self._close_target(target.target_id)

    def close(self) -> None:
        active = list(self._sessions.values())
        self._sessions.clear()
        for target in active:
            self._close_target(target.target_id)

    def interact(
        self,
        handle_id: str,
        *,
        action: str,
        selector: str = "",
        text: str = "",
        key: str = "Enter",
        delta_y: int = 960,
        timeout_ms: int = 15000,
        wait_after_load_ms: int = 900,
    ) -> dict[str, Any]:
        target = self._sessions.get(handle_id)
        if target is None:
            raise KeyError(f"Browser handle not found: {handle_id}")

        expression = ""
        if action == "click":
            if not selector:
                raise ValueError("click action requires selector")
            if selector.startswith("text="):
                match_text = selector[5:]
                expression = f"""
(() => {{
  const wanted = {json.dumps(match_text)};
  const nodes = Array.from(document.querySelectorAll('a,button,[role=\"button\"],div,span,li'));
  const target = nodes.find((node) => ((node.innerText || node.textContent || '').trim() === wanted) || ((node.innerText || node.textContent || '').includes(wanted)));
  if (!target) throw new Error('text_target_not_found');
  target.click();
  return true;
}})()
"""
            else:
                expression = f"""
(() => {{
  const target = document.querySelector({json.dumps(selector)});
  if (!target) throw new Error('selector_not_found');
  target.click();
  return true;
}})()
"""
        elif action == "fill":
            if not selector:
                raise ValueError("fill action requires selector")
            expression = f"""
(() => {{
  const target = document.querySelector({json.dumps(selector)});
  if (!target) throw new Error('selector_not_found');
  target.focus();
  target.value = {json.dumps(text)};
  target.dispatchEvent(new Event('input', {{ bubbles: true }}));
  target.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return true;
}})()
"""
        elif action == "press":
            expression = f"""
(() => {{
  const target = {json.dumps(selector)} ? document.querySelector({json.dumps(selector)}) : document.activeElement;
  if (!target) throw new Error('selector_not_found');
  const key = {json.dumps(key)};
  ['keydown', 'keypress', 'keyup'].forEach((type) => target.dispatchEvent(new KeyboardEvent(type, {{ key, bubbles: true }})));
  if (key === 'Enter' && target.form) target.form.submit();
  return true;
}})()
"""
        elif action == "scroll":
            expression = f"window.scrollBy(0, {int(delta_y)}); true"
        elif action == "goto":
            if not text:
                raise ValueError("goto action requires text=url")
            self._send(target.ws_url, [("Page.enable", {}), ("Runtime.enable", {}), ("Page.navigate", {"url": text})])
            time.sleep(max(0.2, wait_after_load_ms / 1000.0))
            return self.extract_target(target, requested_url=text, capture_screenshot=True, handle_id=handle_id)
        else:
            raise ValueError(f"Unsupported browser action: {action}")

        self._send(target.ws_url, [("Runtime.enable", {}), ("Page.enable", {}), ("Runtime.evaluate", {"expression": expression, "returnByValue": True})])
        time.sleep(max(0.2, wait_after_load_ms / 1000.0))
        return self.extract_target(target, requested_url=target.url or "about:blank", capture_screenshot=True, handle_id=handle_id)

    def extract_target(self, target: CdpTarget, *, requested_url: str, capture_screenshot: bool, handle_id: str = "") -> dict[str, Any]:
        expression = r"""
(() => {
  const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const visibleNodes = Array.from(document.querySelectorAll('main, article, section, p, li, h1, h2, h3, h4, td, th, blockquote, pre, button'));
  const visibleText = [];
  let visibleLength = 0;
  for (const node of visibleNodes) {
    const text = normalize(node.innerText || node.textContent || '');
    if (!text || text.length < 2) continue;
    visibleText.push(text);
    visibleLength += text.length;
    if (visibleLength > 20000) break;
  }
  const headings = Array.from(document.querySelectorAll('h1, h2, h3')).map((node) => normalize(node.innerText || node.textContent || '')).filter(Boolean).slice(0, 12);
  const links = Array.from(document.querySelectorAll('a[href]')).map((node) => ({ text: normalize(node.innerText || node.textContent || ''), url: node.href || '' })).filter((item) => item.url).slice(0, 20);
  const tableRows = [];
  for (const row of Array.from(document.querySelectorAll('table tr')).slice(0, 24)) {
    const cells = Array.from(row.querySelectorAll('th, td')).map((node) => normalize(node.innerText || node.textContent || '')).filter(Boolean);
    if (cells.length >= 2) tableRows.push(cells.join(' | '));
  }
  const keyValues = [];
  for (const item of Array.from(document.querySelectorAll('dt, dd, li, p')).slice(0, 80)) {
    const text = normalize(item.innerText || item.textContent || '');
    if (!text) continue;
    if ((text.includes(':') || text.includes('：')) && text.length <= 180) keyValues.push(text);
  }
  const metaDescription = normalize(
    document.querySelector('meta[name="description"]')?.content ||
    document.querySelector('meta[property="og:description"]')?.content || ''
  );
  return {
    url: location.href,
    title: document.title || '',
    html: document.documentElement.outerHTML || '',
    meta_description: metaDescription,
    visible_text: visibleText.join('\n').slice(0, 20000),
    headings,
    links,
    key_values: keyValues.slice(0, 24),
    table_rows: tableRows.slice(0, 24),
  };
})()
"""
        commands = [("Runtime.enable", {}), ("Page.enable", {}), ("Runtime.evaluate", {"expression": expression, "returnByValue": True})]
        if capture_screenshot:
            commands.append(("Page.captureScreenshot", {"format": "png", "fromSurface": True}))
        results = self._send(target.ws_url, commands)
        payload = results[2].get("result", {}).get("result", {}).get("value", {})
        screenshot_path = ""
        if capture_screenshot and len(results) > 3:
            data = results[3].get("result", {}).get("data", "")
            if data:
                SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                filename = f"browser_{int(time.time() * 1000)}.png"
                screenshot_path = str(SCREENSHOT_DIR / filename)
                Path(screenshot_path).write_bytes(base64.b64decode(data))
        return {
            "requested_url": requested_url,
            "final_url": str(payload.get("url", "")).strip(),
            "title": str(payload.get("title", "")).strip(),
            "html": str(payload.get("html", "") or ""),
            "meta_description": str(payload.get("meta_description", "")).strip(),
            "visible_text": str(payload.get("visible_text", "")).strip(),
            "headings": [str(item).strip() for item in payload.get("headings", []) if str(item).strip()],
            "links": [{"text": str(item.get("text", "")).strip(), "url": str(item.get("url", "")).strip()} for item in payload.get("links", []) if str(item.get("url", "")).strip()],
            "key_values": [str(item).strip() for item in payload.get("key_values", []) if str(item).strip()],
            "table_rows": [str(item).strip() for item in payload.get("table_rows", []) if str(item).strip()],
            "screenshot_path": screenshot_path,
            "handle_id": handle_id,
            "status_code": 200,
            "blocked_reason": "",
        }


def probe_cdp_http_origin(origins: list[str]) -> str:
    for origin in origins:
        probe = origin.rstrip('/')
        try:
            with urlopen(f"{probe}/json/version", timeout=2) as resp:
                data = json.loads(resp.read().decode('utf-8', 'replace'))
            if str(data.get('Browser') or '').strip():
                return probe
        except Exception:
            continue
    return ''

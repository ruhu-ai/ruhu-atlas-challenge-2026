from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from ruhu.browser_tasks import (
    BrowserTaskPack,
    BrowserTaskPackBrowserPlan,
    BrowserTaskPackDomAction,
    BrowserTaskPackDomExtraction,
    BrowserWorkerRequest,
    PlaywrightBrowserWorkerAdapter,
)


class _TaskPageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/orders":
            self.send_response(404)
            self.end_headers()
            return
        body = b"""<!doctype html>
<html>
  <body>
    <input data-ruhu-field="order_id" value="">
    <input type="password" value="super-secret">
    <button data-ruhu-action="search" onclick="
      document.querySelector('[data-ruhu-result=order]').hidden = false;
      document.querySelector('[data-ruhu-result=order_id]').innerText = document.querySelector('[data-ruhu-field=order_id]').value;
      document.querySelector('[data-ruhu-result=status]').innerText = 'Shipped';
      document.querySelector('[data-ruhu-result=tracking_number]').innerText = 'TRACK-456';
      document.querySelector('[data-ruhu-result=estimated_delivery]').innerText = '2026-05-04';
    ">Search</button>
    <section data-ruhu-result="order" hidden>
      <span data-ruhu-result="order_id"></span>
      <span data-ruhu-result="status"></span>
      <span data-ruhu-result="tracking_number"></span>
      <span data-ruhu-result="estimated_delivery"></span>
    </section>
  </body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


@pytest.fixture
def task_page_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TaskPageHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/orders"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _sync_playwright_or_skip():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:
        pytest.skip(f"Playwright Chromium is not installed or cannot launch: {exc}")
    return sync_playwright


def test_playwright_browser_worker_adapter_executes_real_browser_plan(task_page_url: str) -> None:
    sync_playwright = _sync_playwright_or_skip()
    request = BrowserWorkerRequest.from_task_pack(
        request_id="req_real",
        task_id="task_real",
        conversation_id="conv_real",
        title="Lookup order",
        input={"order_id": "ORDER-123"},
        pack=BrowserTaskPack(
            pack_id="lookup_order",
            version="1.0.0",
            display_name="Lookup order",
            allowed_domains=["127.0.0.1"],
            start_url=task_page_url,
            browser_plan=BrowserTaskPackBrowserPlan(
                actions=[
                    BrowserTaskPackDomAction(
                        kind="fill",
                        selector='[data-ruhu-field="order_id"]',
                        value_from_input="order_id",
                    ),
                    BrowserTaskPackDomAction(kind="click", selector='[data-ruhu-action="search"]'),
                    BrowserTaskPackDomAction(kind="wait_for_selector", selector='[data-ruhu-result="order"]'),
                ],
                extractions=[
                    BrowserTaskPackDomExtraction(field="order_id", selector='[data-ruhu-result="order_id"]'),
                    BrowserTaskPackDomExtraction(field="status", selector='[data-ruhu-result="status"]'),
                    BrowserTaskPackDomExtraction(field="tracking_number", selector='[data-ruhu-result="tracking_number"]'),
                    BrowserTaskPackDomExtraction(field="estimated_delivery", selector='[data-ruhu-result="estimated_delivery"]'),
                ],
            ),
        ),
    )
    adapter = PlaywrightBrowserWorkerAdapter(
        headless=True,
        sync_playwright_factory=sync_playwright,
    )
    progress = []

    result = adapter.execute(request, progress.append)

    assert result.success is True
    assert result.output == {
        "order_id": "ORDER-123",
        "status": "Shipped",
        "tracking_number": "TRACK-456",
        "estimated_delivery": "2026-05-04",
    }
    assert result.generated_artifacts
    assert result.generated_artifacts[0].kind == "screenshot"
    assert result.generated_artifacts[0].metadata["redacted"] is True
    assert [event.phase for event in progress] == ["starting", "navigating", "acting", "acting", "completed"]

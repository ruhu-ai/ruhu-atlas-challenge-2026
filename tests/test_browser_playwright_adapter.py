from ruhu.browser_tasks import (
    BrowserCredentialRef,
    BrowserResolvedCredential,
    BrowserTaskPack,
    BrowserWorkerRequest,
    PlaywrightBrowserWorkerAdapter,
)
import ruhu.browser_tasks.playwright_adapter as playwright_adapter_module


class FakeResponse:
    status = 200


class FakePage:
    def __init__(self, final_url: str = "https://merchant.example/orders") -> None:
        self.url = final_url

    def goto(self, url: str, wait_until: str, timeout: int) -> FakeResponse:
        self.url = url if "evil" not in self.url else self.url
        self.wait_until = wait_until
        self.timeout = timeout
        return FakeResponse()

    def title(self) -> str:
        return "Merchant Orders"


class FakeContext:
    def __init__(
        self,
        final_url: str = "https://merchant.example/orders",
        route_handlers: list | None = None,
    ) -> None:
        self.final_url = final_url
        self.closed = False
        self.route_handlers = route_handlers

    def new_page(self) -> FakePage:
        return FakePage(self.final_url)

    def route(self, pattern: str, handler) -> None:
        if self.route_handlers is not None:
            self.route_handlers.append((pattern, handler))

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(
        self,
        final_url: str = "https://merchant.example/orders",
        context_kwargs: dict | None = None,
        route_handlers: list | None = None,
    ) -> None:
        self.final_url = final_url
        self.context_kwargs = context_kwargs
        self.route_handlers = route_handlers
        self.closed = False

    def new_context(self, **kwargs) -> FakeContext:
        self.accept_downloads = kwargs.get("accept_downloads")
        if self.context_kwargs is not None:
            self.context_kwargs.update(kwargs)
        return FakeContext(self.final_url, self.route_handlers)

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(
        self,
        final_url: str = "https://merchant.example/orders",
        context_kwargs: dict | None = None,
        route_handlers: list | None = None,
    ) -> None:
        self.final_url = final_url
        self.context_kwargs = context_kwargs
        self.route_handlers = route_handlers

    def launch(self, headless: bool) -> FakeBrowser:
        self.headless = headless
        return FakeBrowser(self.final_url, self.context_kwargs, self.route_handlers)


class FakePlaywright:
    def __init__(
        self,
        final_url: str = "https://merchant.example/orders",
        context_kwargs: dict | None = None,
        route_handlers: list | None = None,
    ) -> None:
        self.chromium = FakeChromium(final_url, context_kwargs, route_handlers)

    def __enter__(self) -> "FakePlaywright":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _request() -> BrowserWorkerRequest:
    return BrowserWorkerRequest.from_task_pack(
        request_id="req_1",
        task_id="task_1",
        conversation_id="conv_1",
        title="Lookup order",
        pack=BrowserTaskPack(
            pack_id="lookup_order",
            version="1.0.0",
            display_name="Lookup order",
            allowed_domains=["merchant.example"],
            start_url="https://merchant.example/orders",
        ),
    )


class FakeResolver:
    def __init__(self, resolved: BrowserResolvedCredential) -> None:
        self.resolved = resolved

    def resolve(self, *, request: BrowserWorkerRequest, credential: BrowserCredentialRef) -> BrowserResolvedCredential:
        return self.resolved


def test_playwright_browser_worker_adapter_inspects_start_url() -> None:
    progress = []
    adapter = PlaywrightBrowserWorkerAdapter(
        sync_playwright_factory=lambda: FakePlaywright(),
    )

    result = adapter.execute(_request(), progress.append)

    assert result.success is True
    assert result.summary == "Browser task inspection completed."
    assert result.output["final_url"] == "https://merchant.example/orders"
    assert result.output["title"] == "Merchant Orders"
    assert result.output["http_status"] == 200
    assert [event.phase for event in progress] == ["starting", "navigating", "completed"]


def test_playwright_browser_worker_adapter_blocks_disallowed_final_url() -> None:
    adapter = PlaywrightBrowserWorkerAdapter(
        sync_playwright_factory=lambda: FakePlaywright("https://evil.example/orders"),
    )

    result = adapter.execute(_request(), lambda _progress: None)

    assert result.success is False
    assert result.error is not None
    assert result.error.kind == "policy_violation"
    assert result.error.retryable is False


class FakeRouteRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = FakeRouteRequest(url)
        self.action = None

    def continue_(self) -> None:
        self.action = "continue"

    def abort(self) -> None:
        self.action = "abort"


def test_playwright_browser_worker_adapter_installs_request_domain_guard() -> None:
    route_handlers = []
    adapter = PlaywrightBrowserWorkerAdapter(
        sync_playwright_factory=lambda: FakePlaywright(route_handlers=route_handlers),
    )

    result = adapter.execute(_request(), lambda _progress: None)

    assert result.success is True
    assert route_handlers[0][0] == "**/*"
    handler = route_handlers[0][1]
    allowed = FakeRoute("https://merchant.example/orders/1")
    blocked = FakeRoute("https://evil.example/track.js")
    handler(allowed)
    handler(blocked)
    assert allowed.action == "continue"
    assert blocked.action == "abort"


def test_playwright_browser_worker_adapter_fails_closed_without_playwright(monkeypatch) -> None:
    monkeypatch.setattr(playwright_adapter_module, "_load_sync_playwright", lambda: None)
    adapter = PlaywrightBrowserWorkerAdapter(sync_playwright_factory=None)

    result = adapter.execute(_request(), lambda _progress: None)

    assert result.success is False
    assert result.error is not None
    assert result.error.kind == "worker_unavailable"
    assert result.error.retryable is False


def test_playwright_browser_worker_adapter_rejects_credential_refs_until_resolver_exists() -> None:
    request = _request().model_copy(
        update={
            "credentials": [
                BrowserCredentialRef(
                    name="merchant_connection",
                    kind="oauth",
                    secret_ref="connection:conn_123",
                )
            ]
        }
    )
    adapter = PlaywrightBrowserWorkerAdapter(
        sync_playwright_factory=lambda: FakePlaywright(),
    )

    result = adapter.execute(request, lambda _progress: None)

    assert result.success is False
    assert result.error is not None
    assert result.error.kind == "policy_violation"
    assert result.error.retryable is False


def test_playwright_browser_worker_adapter_applies_resolved_session_storage_state() -> None:
    context_kwargs = {}
    request = _request().model_copy(
        update={
            "credentials": [
                BrowserCredentialRef(
                    name="merchant_session",
                    kind="session",
                    secret_ref="connection:conn_123",
                )
            ]
        }
    )
    storage_state = {
        "cookies": [
            {
                "name": "session",
                "value": "redacted",
                "domain": "merchant.example",
                "path": "/",
            }
        ],
        "origins": [],
    }
    adapter = PlaywrightBrowserWorkerAdapter(
        sync_playwright_factory=lambda: FakePlaywright(context_kwargs=context_kwargs),
        credential_resolver=FakeResolver(
            BrowserResolvedCredential(
                name="merchant_session",
                kind="session",
                storage_state=storage_state,
            )
        ),
    )
    progress = []

    result = adapter.execute(request, progress.append)

    assert result.success is True
    assert context_kwargs["storage_state"] == storage_state
    assert [event.phase for event in progress] == ["starting", "authenticating", "navigating", "completed"]


def test_playwright_browser_worker_adapter_rejects_non_session_resolved_credentials() -> None:
    request = _request().model_copy(
        update={
            "credentials": [
                BrowserCredentialRef(
                    name="merchant_connection",
                    kind="oauth",
                    secret_ref="connection:conn_123",
                )
            ]
        }
    )
    adapter = PlaywrightBrowserWorkerAdapter(
        sync_playwright_factory=lambda: FakePlaywright(),
        credential_resolver=FakeResolver(
            BrowserResolvedCredential(
                name="merchant_connection",
                kind="oauth",
                storage_state={"cookies": [], "origins": []},
            )
        ),
    )

    result = adapter.execute(request, lambda _progress: None)

    assert result.success is False
    assert result.error is not None
    assert result.error.kind == "policy_violation"


def test_playwright_browser_worker_adapter_rejects_session_without_storage_state() -> None:
    request = _request().model_copy(
        update={
            "credentials": [
                BrowserCredentialRef(
                    name="merchant_session",
                    kind="session",
                    secret_ref="connection:conn_123",
                )
            ]
        }
    )
    adapter = PlaywrightBrowserWorkerAdapter(
        sync_playwright_factory=lambda: FakePlaywright(),
        credential_resolver=FakeResolver(
            BrowserResolvedCredential(
                name="merchant_session",
                kind="session",
            )
        ),
    )

    result = adapter.execute(request, lambda _progress: None)

    assert result.success is False
    assert result.error is not None
    assert result.error.kind == "policy_violation"

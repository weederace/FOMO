"""Read trader data by crawling a public web page instead of calling an HTTP API.

The provider renders the public FOMO API showcase page with a headless browser and
reads only what any visitor sees: the live leaderboard table and the alert stream.
This project sends no API key and stores no credentials. The target host's
`robots.txt` is fetched and honoured before the first render, and the page is
re-rendered at most once per cache window so the public site is not hammered.
"""

import asyncio
import hashlib
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

import httpx

from app.providers.base import BaseProvider
from app.schemas.trader import NormalizedTrader
from app.utils.robots import robots_allows

LOGGER = logging.getLogger(__name__)

CRAWLER_USER_AGENT = "FomoWhaleIntelligence/0.2 (public page crawl; no credentials)"
CRAWL_PLATFORM = "fomo-crawl"
PROFILE_URL_TEMPLATE = "https://fomo.family/profile/{handle}"
LEADERBOARD_WINDOWS = ("24h", "7d", "30d", "all")
DEFAULT_WINDOW = LEADERBOARD_WINDOWS[0]

LEADERBOARD_ROW_SELECTOR = "#lb tr"
LEADERBOARD_READY_SELECTOR = "#lb tr .rank"
LEADERBOARD_TAB_SELECTOR = ".lb-tab"
ALERT_ROW_SELECTOR = "#alertStream .alert-row"
THESIS_ROW_SELECTOR = "#thesisStream .th-row"

MULTIPLIERS = {"K": Decimal(1_000), "M": Decimal(1_000_000), "B": Decimal(1_000_000_000), "T": Decimal(1_000_000_000_000)}
AMOUNT_PATTERN = re.compile(r"^(?P<sign>[+-])?\$?\s*(?P<digits>\d+(?:\.\d+)?)\s*(?P<suffix>[KMBT])?$", re.IGNORECASE)
TRADE_TEXT_PATTERN = re.compile(r"^(?P<trader>\S+)\s+(?P<action>bought|sold)\s+\$(?P<token>\S+)", re.IGNORECASE)
TRADE_VALUE_PATTERN = re.compile(r"\(\s*(?P<amount>[+-]?\$[\d.,]+\s*[KMBT]?)\s*(?:size|realized)\s*\)", re.IGNORECASE)

# Reads one rendered leaderboard row. Full wallet addresses live in `data-copy`
# because the visible chip text is truncated for display.
LEADERBOARD_ROW_SCRIPT = """
rows => rows.map(row => {
  const cells = Array.from(row.querySelectorAll('td'));
  const cell = index => (cells[index] ? cells[index].textContent.trim() : null);
  const wallets = {};
  row.querySelectorAll('.chip[data-copy]').forEach(chip => {
    const label = (chip.textContent || '').trim().split(/\\s+/)[0].toLowerCase();
    wallets[label] = chip.getAttribute('data-copy');
  });
  const identity = row.querySelector('.h');
  const name = identity ? identity.querySelector('small') : null;
  const handleNode = identity ? identity.firstChild : null;
  const pnlNode = row.querySelector('.pnl');
  const avatar = row.querySelector('.av img');
  return {
    rank: cell(0),
    handle: handleNode ? handleNode.textContent.trim() : null,
    displayName: name ? name.textContent.trim() : null,
    pnl: pnlNode ? pnlNode.textContent.trim() : cell(2),
    volume: cell(3),
    trades: cell(4),
    followers: cell(5),
    holdings: cell(7),
    avatar: avatar ? avatar.getAttribute('src') : null,
    solanaWallet: wallets.sol || null,
    evmWallet: wallets.evm || null,
  };
})
"""

ALERT_ROW_SCRIPT = """
rows => rows.map(row => {
  const pick = selector => {
    const node = row.querySelector(selector);
    return node ? node.textContent.trim() : null;
  };
  return { tag: pick('.tag'), text: pick('.txt'), age: pick('.t') };
})
"""

# A thesis is the trader's own stated reason for a position, with their money on it.
THESIS_ROW_SCRIPT = """
rows => rows.map(row => {
  const pick = selector => {
    const node = row.querySelector(selector);
    return node ? node.textContent.trim() : null;
  };
  const money = {};
  row.querySelectorAll('.th-money span').forEach(span => {
    const bold = span.querySelector('b');
    if (!bold) return;
    const label = span.textContent.replace(bold.textContent, '').trim().toLowerCase();
    money[label] = bold.textContent.trim();
  });
  return {
    handle: pick('.th-who'),
    token: pick('.th-tok'),
    text: pick('.th-text'),
    holding: money.holding || null,
    unrealised: money.unrealised || null,
    realised: money.realised || null,
  };
})
"""


class CrawlProviderError(RuntimeError):
    """The public page could not be rendered or carried no readable data."""


async def install_chromium() -> None:
    """Download the browser the crawl drives. Idempotent, and quick once it is present."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "playwright", "install", "chromium",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    async for raw in process.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if line and not line.startswith("|"):  # skip the progress-bar frames
            LOGGER.info("playwright: %s", line)
    if await process.wait() != 0:
        raise CrawlProviderError("`playwright install chromium` did not finish successfully")


async def _try_launch(playwright: Any, headless: bool, executable_path: str | None) -> Any:
    attempts: list[dict[str, Any]] = []
    if executable_path:
        attempts.append({"executable_path": executable_path})
    # The full build is what `playwright install chromium` provides; the headless shell
    # is a separate download that is often absent.
    attempts.extend(({"channel": "chromium"}, {}))
    failure: Exception | None = None
    for options in attempts:
        try:
            return await playwright.chromium.launch(headless=headless, **options)
        except Exception as exc:
            failure = exc
    raise CrawlProviderError(
        f"No Chromium build could be started ({type(failure).__name__})"
    ) from failure


async def launch_chromium(
    playwright: Any,
    headless: bool = True,
    executable_path: str | None = None,
    auto_install: bool = True,
) -> Any:
    """Start Chromium, downloading it first if this machine does not have it yet."""
    try:
        return await _try_launch(playwright, headless, executable_path)
    except CrawlProviderError:
        if not auto_install:
            raise
        LOGGER.warning("No Chromium build was found. Downloading it now; this happens once.")
        await install_chromium()
        try:
            return await _try_launch(playwright, headless, executable_path)
        except CrawlProviderError as exc:
            raise CrawlProviderError(
                "Chromium still could not be started after installing it. Run "
                "`playwright install chromium` manually, or set BROWSER_EXECUTABLE_PATH "
                "in .env to an existing Chrome or Chromium binary."
            ) from exc


def parse_amount(value: str | None) -> Decimal | None:
    """Read a rendered money or count string such as `+$1,187,274`, `$1.2M`, or `3.4K`."""
    if value is None:
        return None
    text = " ".join(str(value).split()).replace(",", "")
    if not text:
        return None
    match = AMOUNT_PATTERN.match(text)
    if not match:
        return None
    try:
        amount = Decimal(match.group("digits"))
    except InvalidOperation:
        return None
    suffix = match.group("suffix")
    if suffix:
        amount *= MULTIPLIERS[suffix.upper()]
    return -amount if match.group("sign") == "-" else amount


def parse_count(value: str | None) -> int | None:
    amount = parse_amount(value)
    return int(amount) if amount is not None and amount >= 0 else None


def parse_rank(value: str | None) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return None
    rank = int(match.group())
    return rank if rank >= 1 else None


def parse_handle(value: str | None) -> str | None:
    handle = str(value or "").strip().lstrip("@").strip()
    return handle or None


def build_trader(row: dict[str, Any], captured_at: datetime) -> NormalizedTrader | None:
    """Turn one scraped row into the internal normalized shape, or drop it."""
    handle = parse_handle(row.get("handle"))
    if not handle:
        return None
    display_name = str(row.get("displayName") or "").strip() or handle
    volume = parse_amount(row.get("volume"))
    return NormalizedTrader(
        platform=CRAWL_PLATFORM,
        platform_trader_id=handle,
        username=handle,
        display_name=display_name,
        profile_url=PROFILE_URL_TEMPLATE.format(handle=handle),
        wallet_address=row.get("evmWallet") or row.get("solanaWallet") or None,
        wallets={key: value for key, value in {
            "evm": row.get("evmWallet"), "solana": row.get("solanaWallet")
        }.items() if value},
        rank=parse_rank(row.get("rank")),
        pnl=parse_amount(row.get("pnl")),
        volume=volume if volume is not None and volume >= 0 else None,
        follower_count=parse_count(row.get("followers")),
        total_trades=parse_count(row.get("trades")),
        captured_at=captured_at,
    )


def alert_event_id(alert_type: str, text: str) -> str:
    """Stable id for a rendered alert; the page shows relative ages, not timestamps."""
    return hashlib.sha256(f"{alert_type}|{text}".encode()).hexdigest()[:16]


def build_alert(row: dict[str, Any], captured_at: datetime) -> dict[str, Any] | None:
    text = " ".join(str(row.get("text") or "").split())
    if not text:
        return None
    alert_type = str(row.get("tag") or "").strip().lower() or "alert"
    payload: dict[str, Any] = {
        "id": alert_event_id(alert_type, text),
        "type": alert_type,
        "text": text,
        "age": str(row.get("age") or "").strip() or None,
        "source": CRAWL_PLATFORM,
        "capturedAt": captured_at.isoformat(),
    }
    trade = TRADE_TEXT_PATTERN.match(text)
    if trade:
        payload["trader"] = trade.group("trader")
        payload["token"] = trade.group("token")
        payload["side"] = trade.group("action").lower()
    value = TRADE_VALUE_PATTERN.search(text)
    if value:
        amount = parse_amount(value.group("amount"))
        if amount is not None:
            payload["usdValue"] = float(amount)
    return payload


def build_thesis(row: dict[str, Any], captured_at: datetime) -> dict[str, Any] | None:
    handle = parse_handle(row.get("handle"))
    text = " ".join(str(row.get("text") or "").split())
    if not handle or not text:
        return None
    token = str(row.get("token") or "").strip().lstrip("$").strip()
    payload: dict[str, Any] = {
        "handle": handle,
        "token": token or None,
        "text": text,
        "capturedAt": captured_at.isoformat(),
    }
    for name, source in (("holdingUsd", "holding"), ("unrealizedPnlUsd", "unrealised"), ("realizedPnlUsd", "realised")):
        amount = parse_amount(row.get(source))
        if amount is not None:
            payload[name] = float(amount)
    return payload


@dataclass
class SiteScan:
    """Everything one render of the public page could legitimately show."""

    url: str
    captured_at: datetime
    windows: dict[str, list[NormalizedTrader]] = field(default_factory=dict)
    alerts: list[dict] = field(default_factory=list)
    theses: list[dict] = field(default_factory=list)

    def unique_traders(self) -> dict[str, NormalizedTrader]:
        """One row per handle, keeping the appearance with the largest PnL."""
        best: dict[str, NormalizedTrader] = {}
        for traders in self.windows.values():
            for trader in traders:
                current = best.get(trader.platform_trader_id)
                if current is None or (trader.pnl or 0) > (current.pnl or 0):
                    best[trader.platform_trader_id] = trader
        return best

    def placements(self) -> dict[str, list[tuple[str, int | None]]]:
        """Which windows each handle appears in, and at what rank."""
        seen: dict[str, list[tuple[str, int | None]]] = {}
        for window, traders in self.windows.items():
            for trader in traders:
                seen.setdefault(trader.platform_trader_id, []).append((window, trader.rank))
        return seen


class CrawlProvider(BaseProvider):
    """Collect public trader data by rendering a public page, not by calling an API."""

    name = CRAWL_PLATFORM

    def __init__(
        self,
        url: str = "https://fomoapi.io/",
        window: str = DEFAULT_WINDOW,
        limit: int = 10,
        executable_path: str | None = None,
        headless: bool = True,
        render_timeout_seconds: float = 45,
        cache_seconds: int = 60,
        alert_limit: int = 25,
        respect_robots: bool = True,
    ) -> None:
        if window not in LEADERBOARD_WINDOWS:
            raise ValueError(f"window must be one of {', '.join(LEADERBOARD_WINDOWS)}")
        self.url = url
        self.window = window
        self.limit = limit
        self.executable_path = executable_path or None
        self.headless = headless
        self.render_timeout_seconds = render_timeout_seconds
        self.cache_seconds = cache_seconds
        self.alert_limit = alert_limit
        self.respect_robots = respect_robots
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._cache: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._robots_checked = False
        self.render_count = 0
        self.success_count = 0
        self.error_count = 0
        self.last_row_count: int | None = None

    def capabilities(self) -> dict[str, bool]:
        return {
            "leaderboard": True,
            "user_profile": False,
            "trades": False,
            "balances": False,
            "alerts": True,
            "realtime": False,
        }

    def request_metrics(self) -> dict[str, int | None]:
        return {
            "request_count": self.render_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "last_status_code": None,
            "last_row_count": self.last_row_count,
        }

    async def _ensure_allowed(self) -> None:
        if not self.respect_robots or self._robots_checked:
            return
        try:
            async with httpx.AsyncClient(
                timeout=15, trust_env=False, headers={"user-agent": CRAWLER_USER_AGENT}
            ) as client:
                response = await client.get(urljoin(self.url, "/robots.txt"))
        except httpx.HTTPError as exc:
            raise CrawlProviderError(f"Could not read robots.txt: {type(exc).__name__}") from exc
        # A missing robots.txt is an empty rule set, which allows everything.
        if not robots_allows(response.text if response.status_code == 200 else "", self.url):
            raise CrawlProviderError(f"robots.txt disallows crawling {self.url}")
        self._robots_checked = True

    async def _launch(self) -> Any:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise CrawlProviderError("Playwright is not installed") from exc
        self._playwright = await async_playwright().start()
        try:
            return await launch_chromium(self._playwright, self.headless, self.executable_path)
        except CrawlProviderError:
            await self._stop_playwright()
            raise

    async def _browser_instance(self) -> Any:
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        self._browser = await self._launch()
        return self._browser

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # a dead driver must not mask the original failure
                LOGGER.debug("Playwright driver was already stopped")
            self._playwright = None

    async def _close_browser(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # the browser may already have crashed or exited
                LOGGER.debug("Crawl browser was already closed")
            self._browser = None
        await self._stop_playwright()

    async def _read_rows(self, page: Any, selector: str, script: str, timeout_ms: int = 10_000) -> list[dict]:
        """Read an optional widget: an empty one is normal, not a failure."""
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
        except PlaywrightTimeoutError:
            LOGGER.info("Public page rendered nothing for %s url=%s", selector, self.url)
            return []
        return await page.eval_on_selector_all(selector, script)

    async def _scrape(self, windows: tuple[str, ...], with_theses: bool) -> dict[str, Any]:
        timeout_ms = int(self.render_timeout_seconds * 1000)
        browser = await self._browser_instance()
        page = await browser.new_page(viewport={"width": 1440, "height": 1400})
        try:
            await page.goto(self.url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_selector(LEADERBOARD_READY_SELECTOR, timeout=timeout_ms)
            captured_at = datetime.now(UTC)
            shown = DEFAULT_WINDOW
            rows: dict[str, list[dict]] = {}
            for window in windows:
                if window != shown:
                    await self._select_window(page, window, timeout_ms)
                    shown = window
                rows[window] = await page.eval_on_selector_all(LEADERBOARD_ROW_SELECTOR, LEADERBOARD_ROW_SCRIPT)
            alerts = await self._read_rows(page, ALERT_ROW_SELECTOR, ALERT_ROW_SCRIPT)
            theses = await self._read_rows(page, THESIS_ROW_SELECTOR, THESIS_ROW_SCRIPT) if with_theses else []
            return {"rows": rows, "alerts": alerts, "theses": theses, "captured_at": captured_at}
        finally:
            await page.close()

    async def _select_window(self, page: Any, window: str, timeout_ms: int) -> None:
        """Switch the rendered leaderboard to another window and wait for its data."""
        tab = page.locator(f'{LEADERBOARD_TAB_SELECTOR}[data-win="{window}"]')
        if not await tab.count():
            raise CrawlProviderError(f"The public page has no {window} leaderboard tab")
        async with page.expect_response(
            lambda response: f"/leaderboard/{window}" in response.url, timeout=timeout_ms
        ):
            await tab.first.click()
        await page.wait_for_timeout(1000)

    async def _render(self, windows: tuple[str, ...], with_theses: bool = False) -> dict[str, Any]:
        await self._ensure_allowed()
        failure: Exception | None = None
        for attempt in range(2):
            self.render_count += 1
            try:
                payload = await self._scrape(windows, with_theses)
                self.success_count += 1
                return payload
            except Exception as exc:
                failure = exc
                self.error_count += 1
                LOGGER.warning(
                    "Public page render failed url=%s attempt=%d error=%s",
                    self.url, attempt + 1, type(exc).__name__,
                )
                await self._close_browser()
        raise CrawlProviderError(f"Public page render failed: {type(failure).__name__}") from failure

    async def _cached_render(self) -> dict[str, Any]:
        async with self._lock:
            now = time.monotonic()
            if self._cache is not None and now - self._cached_at < self.cache_seconds:
                return self._cache
            payload = await self._render((self.window,))
            self._cache, self._cached_at = payload, now
            return payload

    async def get_leaderboard(self, window: str | None = None, limit: int | None = None) -> list[NormalizedTrader]:
        if window is not None and window != self.window:
            raise CrawlProviderError(
                f"This crawl renders one window; set CRAWL_LEADERBOARD_WINDOW={window} to read it"
            )
        payload = await self._cached_render()
        captured_at = payload["captured_at"]
        rows = payload["rows"][self.window]
        traders = [item for row in rows if (item := build_trader(row, captured_at))]
        if not traders:
            raise CrawlProviderError("The public page rendered no leaderboard rows")
        self.last_row_count = len(traders)
        cap = self.limit if limit is None else limit
        return traders[:cap]

    async def scan(self, windows: tuple[str, ...] = LEADERBOARD_WINDOWS) -> SiteScan:
        """Read every public widget in one page session: all windows, alerts, theses."""
        unknown = [window for window in windows if window not in LEADERBOARD_WINDOWS]
        if unknown:
            raise CrawlProviderError(f"Unknown leaderboard window: {', '.join(unknown)}")
        async with self._lock:
            payload = await self._render(tuple(windows), with_theses=True)
        captured_at = payload["captured_at"]
        boards = {
            window: [item for row in rows if (item := build_trader(row, captured_at))]
            for window, rows in payload["rows"].items()
        }
        if not any(boards.values()):
            raise CrawlProviderError("The public page rendered no leaderboard rows")
        self.last_row_count = sum(len(traders) for traders in boards.values())
        return SiteScan(
            url=self.url,
            captured_at=captured_at,
            windows=boards,
            alerts=[item for row in payload["alerts"] if (item := build_alert(row, captured_at))],
            theses=[item for row in payload["theses"] if (item := build_thesis(row, captured_at))],
        )

    async def get_alerts(self, limit: int = 50) -> list[dict]:
        payload = await self._cached_render()
        captured_at = payload["captured_at"]
        alerts = [item for row in payload["alerts"] if (item := build_alert(row, captured_at))]
        return alerts[: min(limit, self.alert_limit)]

    async def aclose(self) -> None:
        await self._close_browser()

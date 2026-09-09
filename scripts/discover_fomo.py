"""Observe public browser traffic; never persist credentials or authentication material."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings
from app.providers.crawl import launch_chromium
from app.utils.logging import configure_logging

LOGGER = logging.getLogger(__name__)
SENSITIVE = ("cookie", "authorization", "token", "password", "secret", "session")


def safe_url(value: str) -> str:
    parts = urlsplit(value)
    return parts._replace(query="", fragment="").geturl()


async def discover(url: str, output: Path) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        LOGGER.error("Playwright is not installed")
        return 2
    records: list[dict[str, object]] = []
    settings = get_settings()
    try:
        async with async_playwright() as playwright:
            browser = await launch_chromium(
                playwright, settings.browser_headless, settings.browser_executable_path
            )
            page = await browser.new_page()

            async def request_seen(request: object) -> None:
                req = request
                headers = getattr(req, "headers", {})
                if any(key.lower() in SENSITIVE for key in headers):
                    return
                resource = getattr(req, "resource_type", "")
                if resource in {"xhr", "fetch", "document"}:
                    records.append(
                        {
                            "endpoint": safe_url(getattr(req, "url", "")),
                            "method": getattr(req, "method", ""),
                            "resource_type": resource,
                        }
                    )

            page.on("request", request_seen)
            page.on(
                "websocket",
                lambda ws: records.append({"websocket_url": safe_url(ws.url), "events": []}),
            )
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if response:
                records.append(
                    {
                        "endpoint": safe_url(response.url),
                        "method": "GET",
                        "content_type": response.headers.get("content-type", ""),
                        "status": response.status,
                        "public_access": response.status < 400,
                    }
                )
            await page.wait_for_timeout(3000)
            await browser.close()
    except Exception as exc:  # browser/network failures are expected and documented
        LOGGER.error("Discovery failed without bypass attempts: %s", type(exc).__name__)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"url": url, "records": records}, indent=2), encoding="utf-8")
    LOGGER.info("Recorded %d public observations to %s", len(records), output)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://fomo.family/")
    parser.add_argument("--output", type=Path, default=Path("docs/discovery.json"))
    args = parser.parse_args()
    configure_logging()
    raise SystemExit(asyncio.run(discover(args.url, args.output)))


if __name__ == "__main__":
    main()

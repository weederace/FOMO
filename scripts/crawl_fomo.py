"""Crawl only public FOMO HTML and assets allowed by robots.txt.

This crawler is intentionally a content/discovery tool, not a trading collector. It
does not send credentials, cookies, authorization headers, or requests to disallowed
paths. It records metadata and short public-text excerpts, never raw response bodies.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.logging import configure_logging
from app.utils.robots import robots_allows

LOGGER = logging.getLogger(__name__)
SENSITIVE_PATHS = ("/export-key", "/verify-token", "/download", "/token")
KEYWORDS = ("api", "graphql", "leaderboard", "trader", "profile", "portfolio", "trade", "transaction", "socket", "fomo.family")
PATH_PATTERN = re.compile(r"(?:https?://[^\"'\s]+|/[A-Za-z0-9_./:${}-]{3,})")


def clean_url(value: str, base: str) -> str | None:
    value = value.strip()
    absolute = urljoin(base, value)
    parts = urlsplit(urldefrag(absolute).url)
    if parts.scheme not in {"http", "https"} or parts.netloc != urlsplit(base).netloc:
        return None
    path = parts.path or "/"
    if any(path.startswith(blocked) for blocked in SENSITIVE_PATHS):
        return None
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class PageParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__()
        self.page_url = page_url
        self.title = ""
        self.description = ""
        self.links: set[str] = set()
        self.scripts: set[str] = set()
        self._in_title = False
        self._ignored_depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        values = dict(attrs)
        if tag == "title":
            self._in_title = True
        href = values.get("href") or values.get("src")
        if tag == "script" and href:
            self.scripts.add(urljoin(self.page_url, href))
        if tag == "link" and href and "modulepreload" in values.get("rel", ""):
            self.scripts.add(urljoin(self.page_url, href))
        if tag == "a" and href:
            self.links.add(href)
        if tag == "meta" and values.get("name", "").lower() == "description":
            self.description = values.get("content", "")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title = f"{self.title} {text}".strip()
        self._text.append(text)

    @property
    def text_excerpt(self) -> str:
        return " ".join(self._text)[:1000]


@dataclass
class CrawlResult:
    url: str
    status: int | None
    content_type: str | None
    title: str | None = None
    description: str | None = None
    text_excerpt: str | None = None
    links: list[str] | None = None
    scripts: list[str] | None = None
    keyword_hits: list[str] | None = None
    candidate_paths: list[str] | None = None
    error: str | None = None


class PublicFomoCrawler:
    def __init__(self, base_url: str, max_pages: int, max_assets: int, delay: float) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.max_pages = max_pages
        self.max_assets = max_assets
        self.delay = delay
        self.client: httpx.AsyncClient | None = None
        self.robots_text = ""

    async def _get(self, url: str) -> httpx.Response:
        assert self.client is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.get(url)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    return response
                await asyncio.sleep(2**attempt)
            except httpx.HTTPError as exc:
                last_error = exc
                await asyncio.sleep(2**attempt)
        if last_error:
            raise last_error
        return response

    async def _load_robots(self) -> None:
        try:
            response = await self._get(urljoin(self.base_url, "/robots.txt"))
            self.robots_text = response.text if response.status_code == 200 else ""
        except (httpx.HTTPError, UnboundLocalError) as exc:
            LOGGER.warning("Could not read robots.txt: %s", type(exc).__name__)

    async def _sitemap_urls(self) -> list[str]:
        try:
            response = await self._get(urljoin(self.base_url, "/sitemap.xml"))
            root = ET.fromstring(response.text)
            return [url for element in root.iter() if element.tag.endswith("loc") and (url := element.text)]
        except (httpx.HTTPError, ET.ParseError):
            return []

    def _allowed(self, url: str) -> bool:
        return robots_allows(self.robots_text, url)

    async def crawl(self) -> dict[str, object]:
        headers = {"User-Agent": "FomoWhaleIntelligence/0.1 (public research; no credentials)"}
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20) as client:
            self.client = client
            await self._load_robots()
            sitemap = [clean_url(url, self.base_url) for url in await self._sitemap_urls()]
            queue = [url for url in [clean_url(self.base_url, self.base_url), *sitemap] if url and self._allowed(url)]
            pages: list[CrawlResult] = []
            assets: list[CrawlResult] = []
            seen: set[str] = set()
            while queue and len(pages) < self.max_pages:
                url = queue.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                try:
                    response = await self._get(url)
                    result = CrawlResult(url, response.status_code, response.headers.get("content-type"))
                    if "html" in response.headers.get("content-type", ""):
                        parser = PageParser(url)
                        parser.feed(response.text)
                        searchable = f"{parser.title} {parser.description} {parser.text_excerpt}".lower()
                        result.title = parser.title or None
                        result.description = parser.description or None
                        result.text_excerpt = parser.text_excerpt or None
                        result.links = sorted({clean_url(link, url) for link in parser.links if clean_url(link, url)})
                        result.scripts = sorted(parser.scripts)
                        result.keyword_hits = sorted({keyword for keyword in KEYWORDS if keyword in searchable})
                        for link in result.links:
                            if link and link not in seen and self._allowed(link):
                                queue.append(link)
                        prioritized_scripts = sorted(
                            result.scripts,
                            key=lambda script: (
                                0
                                if any(
                                    name in script.lower()
                                    for name in ("fomofetch", "manifest", "home", "profile", "trade", "leaderboard")
                                )
                                else 1,
                                script,
                            ),
                        )
                        for script in prioritized_scripts[: self.max_assets]:
                            if urlsplit(script).netloc != urlsplit(self.base_url).netloc:
                                continue
                            if script not in {item.url for item in assets} and self._allowed(script):
                                try:
                                    asset_response = await self._get(script)
                                    body = asset_response.text[:500_000]
                                    hits = sorted({keyword for keyword in KEYWORDS if keyword in body.lower()})
                                    paths = sorted({match for match in PATH_PATTERN.findall(body) if any(keyword in match.lower() for keyword in KEYWORDS)})[:100]
                                    assets.append(CrawlResult(script, asset_response.status_code, asset_response.headers.get("content-type"), keyword_hits=hits, candidate_paths=paths))
                                except httpx.HTTPError as exc:
                                    assets.append(CrawlResult(script, None, None, error=type(exc).__name__))
                    pages.append(result)
                except httpx.HTTPError as exc:
                    pages.append(CrawlResult(url, None, None, error=type(exc).__name__))
                await asyncio.sleep(self.delay)
            return {"base_url": self.base_url, "pages": [result.__dict__ for result in pages], "assets": [result.__dict__ for result in assets]}


async def main_async(args: argparse.Namespace) -> int:
    crawler = PublicFomoCrawler(args.url, args.max_pages, args.max_assets, args.delay)
    result = await crawler.crawl()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    LOGGER.info("Crawled %d pages and %d assets", len(result["pages"]), len(result["assets"]))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://fomo.family/")
    parser.add_argument("--output", type=Path, default=Path("docs/fomo_crawl.json"))
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--max-assets", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()
    configure_logging()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()

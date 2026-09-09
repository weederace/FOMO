# Architecture

`CrawlProvider` is the default source. It renders the configured public page with
Playwright and reads only visible DOM: the leaderboard table and the alert stream. It
calls no API from this project, sends no key, reads no cookies, and bypasses no login,
CAPTCHA, or access control. It fetches the target host's `robots.txt` and refuses to
render if crawling is disallowed. If the page renders no leaderboard rows, collection
fails clearly instead of falling back to API or mock data.

`FomoProvider` remains available as an opt-in alternative and uses the documented
independent FOMOAPI service at `api.fomoapi.io` with `FOMO_API_KEY`. It does not use
the unverified internal `prod-api.fomo.family` candidate. Selection is explicit through
`DATA_PROVIDER`; the two never fall back to one another.

```text
public page render (one per CRAWL_CACHE_SECONDS)
  -> visible leaderboard rows and alert rows
  -> normalized Pydantic data
  -> Trader / TraderSnapshot
  -> metrics and Smart Whale Score
  -> historical TraderScore
  -> rankings and emerging analysis
  -> AlertEvent persistence and optional Telegram
```

Under `DATA_PROVIDER=fomoapi` the same pipeline additionally collects profiles, trades,
and balances, which drive the trade-derived score components. The crawl path reports
`trades: false` and `balances: false` from `capabilities()`, so `IngestionService` skips
those calls rather than failing per trader.

`CrawlProvider` keeps one browser process alive across collection cycles and reuses it,
tearing it down and retrying once if a render fails. `aclose()` shuts it down; the worker
and the FastAPI capability endpoint both call it. If no Chromium build can be started it
runs `playwright install chromium` itself and retries once, so a fresh machine or a
copied project folder repairs itself rather than failing the run.

`CrawlProvider.scan()` is the research path: one page session reads every leaderboard
window plus the alert and thesis streams and returns a `SiteScan`. It bypasses the
collection cache and writes nothing to the database; `scripts/research.py` renders it
into a text report.

`run_worker.py` performs bounded provider requests and serialized database writes.
Redis is used for alert deduplication when available, with an in-memory fallback.
Rendered alerts carry a content-derived id, so repeated identical rows deduplicate.
FastAPI exposes normalized application data without provider credentials.

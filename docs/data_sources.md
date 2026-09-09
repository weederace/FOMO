# Data Sources

Verified 2026-09-07.

| Source | Protocol | Authentication | Status |
| --- | --- | --- | --- |
| `https://fomoapi.io/` rendered leaderboard table | HTTPS/browser | None sent by this project | Confirmed; crawl returned 10 normalized rows per window |
| `https://fomoapi.io/` rendered alert stream | HTTPS/browser | None sent by this project | Confirmed; crawl returned live buy/sell/perp rows |
| `https://fomoapi.io/` rendered thesis stream | HTTPS/browser | None sent by this project | Confirmed; trader, token, reasoning, and position size |
| `https://fomoapi.io/robots.txt` | HTTPS | None | Confirmed `Allow: /` for all agents |
| FOMOAPI `/v2/leaderboard/{window}` | HTTPS | Bearer key | Documented; keyless test now returns HTTP 401 |
| FOMOAPI `/v2/alerts` | HTTPS | Bearer key | Documented; keyless test now returns HTTP 401 |
| FOMOAPI `/v2/users/{handle}` | HTTPS | Bearer key | Documented; used only when `DATA_PROVIDER=fomoapi` |
| FOMOAPI `/v2/users/{handle}/trades` | HTTPS | Bearer key | Documented; used only when `DATA_PROVIDER=fomoapi` |
| FOMOAPI `/v2/users/{handle}/balances` | HTTPS | Bearer key | Documented; used only when `DATA_PROVIDER=fomoapi` |
| FOMOAPI WebSocket alerts | WSS | Key/plan dependent | Documented, not used |
| `https://fomo.family/` public HTML | HTTPS/browser | None | Confirmed readable, but marketing and blog content only |
| `https://fomo.family/` app routes | HTTPS/browser | Login required | Confirmed to render no trader data unauthenticated |
| `https://prod-api.fomo.family` | HTTP | Unknown | Unverified internal candidate; HTTP 430 |
| Mock provider | In-process | None | Confirmed fictional test data |

## What the crawl reads

| Field | Precision | Notes |
| --- | --- | --- |
| rank | Exact | Position within the rendered window |
| handle, display name | Exact | Read from the identity cell |
| PnL | Exact | Rendered in full, e.g. `+$1,187,274` |
| trade count | Exact | Rendered in full, e.g. `3,255` |
| Solana and EVM wallet | Exact | Taken from the chip `data-copy` attribute, not the truncated label |
| volume | Compact | Rendered as `$1.2M`; parsed at that precision |
| follower count | Compact | Rendered as `3.4K`; parsed at that precision |
| holdings count | Not stored | Rendered but outside the normalized schema |
| thesis text and position | Exact / compact | Scan only; handle, token, reasoning, holding and PnL figures |
| per-trade history, balances | Unavailable | Not shown on the public page |

`CrawlProvider.get_leaderboard()` reads one configured window for the collection
pipeline. `CrawlProvider.scan()` reads every window plus the alert and thesis streams in
one page session; `scripts/research.py` turns that into a text report under `reports/`.

FOMOAPI is an independent unofficial provider and is not described as an official
FOMO Labs API. No access-control or anti-bot bypass is attempted, no credential is
sent by the crawl, and no disallowed path is fetched.

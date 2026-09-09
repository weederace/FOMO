# Public Crawl Report

Run date: 2026-09-07. All findings are from public HTML, public JavaScript bundles,
`robots.txt`, `sitemap.xml`, and headless renders without cookies or credentials.

## fomo.family is not a usable crawl target

| Check | Result |
| --- | --- |
| `robots.txt` | HTTP 200; `Allow: /` with `/profile/`, `/user`, `/u/`, `/token`, `/coin`, `/prices/` disallowed |
| `sitemap.xml` | HTTP 200; 146 URLs, all marketing, blog, and `/answers` content |
| Homepage HTML | HTTP 200; contains no `<table>` and no trader rows, only `LEADERBOARD`/`FEED`/`ALERTS` marketing copy |
| `/leaderboard` | Renders the app's "page doesn't exist" view; no such route |
| `/clans`, `/perp`, `/clan/{id}` | SPA shell; renders only a Login button and the footer, and issues no data request |
| `/profile/{handle}` | Renders the marketing landing page; also disallowed by `robots.txt` |
| `app.fomo.family`, `web.fomo.family` | DNS does not resolve |
| `prod-api.fomo.family` | HTTP 430 `{"error":"unauthorized"}` on `/` and `/api/v2/leaderboard` |

The client bundle's route manifest lists `clan`, `perp`, `profile`, `token`, and
redirect routes. Every data-bearing route is behind login. A crawl of `fomo.family`
therefore yields marketing content and bundle metadata, not a trader dataset. The
previous `BrowserFomoProvider`, which looked for a leaderboard table on the homepage,
could never have found one; it has been removed.

## fomoapi.io renders the data publicly

| Check | Result |
| --- | --- |
| `robots.txt` | HTTP 200; `Allow: /` for `*` and explicitly for AI crawlers |
| Homepage | HTTP 200; contains a real `<table>` with `<tbody id="lb">` |
| Rendered leaderboard | 10 rows per window, tabs for `24h`, `7d`, `30d`, `all` |
| Rendered alerts | `#alertStream .alert-row` entries with a type tag, text, and relative age |
| Wallets | Truncated in the visible label, full address in the `data-copy` attribute |

A rendered `24h` row looks like this in the DOM:

```text
<td><span class="rank top">01</span></td>
<td><div class="tr">…<span class="h">@Proteus<small>Proteus</small></span></div></td>
<td class="num"><span class="pnl">+$1,187,274</span></td>
<td class="num">$1.2M</td>      <!-- volume, compact -->
<td class="num">42</td>          <!-- trades -->
<td class="num">3.4K</td>        <!-- followers, compact -->
<td><span class="chip sol" data-copy="Goj6…">SOL Goj6…SBBx</span>…</td>
<td class="num">5</td>           <!-- holdings -->
```

The page fills that table itself from its own publicly documented showcase traffic.
This project reads the rendered result; it sends no key of its own and does not call
the API directly in crawl mode.

## Crawl policy

`CrawlProvider` fetches `robots.txt` once per process and refuses to render when the
target URL is disallowed (`CRAWL_RESPECT_ROBOTS=true` by default). Matching goes
through `app/utils/robots.py`, not `urllib.robotparser`: the standard library returns
the first matching rule, so `fomo.family`'s file — which opens with `Allow: /` and then
lists its exclusions — reported `/profile/` and `/token` as allowed. The project now
applies RFC 9309 precedence, where the longest matching pattern wins and Allow wins a
tie, which reads those files the way their authors meant them. `scripts/crawl_fomo.py`
uses the same matcher. It re-renders at
most once per `CRAWL_CACHE_SECONDS` (default 60), matching the page's own refresh
budget, and reuses a single browser process across cycles. It stores only the
normalized fields listed in `docs/data_sources.md`, never raw response bodies.

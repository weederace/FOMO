# FOMO Endpoint Research

Research date: 2026-09-07. All findings below are from publicly readable website
HTML, public JavaScript bundles, `robots.txt`, and unauthenticated HTTP requests.
No cookies, authorization headers, or credentials were used.

## FOMOAPI Provider

`https://fomoapi.io/docs` documents an independent, unofficial FOMO data service.
Its documented base URL is `https://api.fomoapi.io`, with `GET /v2/leaderboard/{window}`
(`24h`, `7d`, `30d`, `all`) and per-trader routes under `/v2/users/{handle}`.
The docs specify JSON response fields and Bearer authentication. This is a confirmed
**documented external provider**, not an official FOMO Labs API. Its homepage renders
the leaderboard and alert feed publicly, which is what the crawl provider reads.

The project uses `DATA_PROVIDER=fomo` or `DATA_PROVIDER=fomoapi` with
`FOMO_API_BASE_URL` and the secret-only environment variable `FOMO_API_KEY`.
The key is never committed, logged, or embedded in requests outside the provider.

On 2026-09-07, keyless `GET https://api.fomoapi.io/v2/leaderboard/24h?limit=2` returned
HTTP 401 with `"API key required"`. The keyless leaderboard and `/v2/alerts` tiers
recorded in earlier revisions of this document are gone; every endpoint now needs a
Bearer key. With a configured key the same request returns HTTP 200 with
`source: "fomo-live"`, `capturedAt`, and normalized trader rows. The provider parser
accepts null wallet values and ignores provider-specific fields that are not part of
the internal normalized schema.

That change is why the project's default source is now `DATA_PROVIDER=crawl`, which
reads the publicly rendered leaderboard and alert stream on `https://fomoapi.io/`
without sending a key. `DATA_PROVIDER=fomoapi` stays available for profile, trade, and
balance collection, which the public page does not show; it requires `FOMO_API_KEY`.
See `docs/public_crawl_report.md` for the rendered-page evidence.

## Candidate Base

| Candidate | Evidence | Classification | Result |
| --- | --- | --- | --- |
| `https://prod-api.fomo.family` | `fomoFetch-v2-b0ViftSr.js` contains a function returning this base | PUBLIC BUT UNVERIFIED | Direct requests returned HTTP 430 |
| `/api/v2/${e}` | Same bundle contains a parameterized path fragment | PUBLIC BUT UNVERIFIED | No concrete value or unauthenticated response observed |

The public site HTML references `/assets/fomoFetch-v2-b0ViftSr.js` and a React route
bundle. The bundle evidence establishes a client base URL, not permission to use an
undocumented endpoint or a stable response contract.

The public crawl also fetched `fomoFetch-v2-b0ViftSr.js` with HTTP 200 and recorded
the candidate base and parameterized path without storing the raw JavaScript body.
The candidate path is not enough to infer a leaderboard endpoint.

## Candidate Paths

The profile bundle exposes these client-side path strings:

| Path | Evidence / inferred purpose | Classification |
| --- | --- | --- |
| `/profile/${s.userHandle}` | Public route construction in profile UI | PUBLIC BUT UNVERIFIED |
| `/transfers/v2/supportedTokens` | Client call in profile-related bundle | PUBLIC BUT UNVERIFIED; not trader analytics |
| `/transfers/v2/send` | Client POST for transfers | UNSUPPORTED for this project; trading/transfer action |
| `/v2/transfers/with/${n}` | Client transfer-history call | PUBLIC BUT UNVERIFIED; not leaderboard data |

No concrete public leaderboard, trader analytics, portfolio, or trade-history API
path was confirmed. No `ws://` or `wss://` URL was found. GraphQL references were
from Datadog instrumentation and did not identify an application GraphQL endpoint.

## Access Limitations

- `https://fomo.family/` is readable through the normal public web route used for research.
- Direct requests to `https://prod-api.fomo.family/` and `/api/v2/leaderboard` returned
  HTTP 430 `{"error":"unauthorized"}`.
- Headless renders of `/leaderboard`, `/clans`, `/perp`, `/clan/{id}`, and
  `/profile/{handle}` produced no trader data; every data route is behind login.
- `robots.txt` allows `/` but disallows sensitive areas including `/profile/`, `/user`,
  `/u/`, `/token`, `/download`, and `/export-key`. Disallowed paths were not collected.

## Decision

`prod-api.fomo.family` remains unsupported. The application must not select it, must
not guess endpoint paths, and must not silently use mock data when a live provider is
selected. Trader data comes from `DATA_PROVIDER=crawl` reading the public
`fomoapi.io` page, or from `DATA_PROVIDER=fomoapi` with an explicit `FOMO_API_KEY`.

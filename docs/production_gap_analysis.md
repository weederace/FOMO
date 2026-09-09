# Production Gap Analysis

Audited and extended on 2026-09-07. The repository now has a working keyless crawl
provider, an opt-in documented API provider, historical persistence, ingestion worker,
metrics, alerts, and API.

## Working

- Keyless crawl ingestion of the public `fomoapi.io` leaderboard, 10 rows per window
- Keyless crawl ingestion of the public alert stream, deduplicated by content id
- Optional profile, trade, and balance collection with `DATA_PROVIDER=fomoapi` and `FOMO_API_KEY`
- Immutable trader snapshots, trades, balances, score history, and collection runs
- Pydantic response validation, retries, rate limiting, and safe provider errors
- Trade metrics, confidence-aware Smart Whale Score, and emerging detection
- Rankings, provider capabilities/status, REST API, Redis deduplication fallback
- Mock provider, Docker services, Telegram-disabled mode, and CLI scripts

## Real, Mocked, and Unsupported

- The crawled leaderboard and alert stream are real and live-tested end to end.
- FOMOAPI now requires a key on every endpoint, including the leaderboard; keyless
  requests were re-tested on 2026-09-07 and returned HTTP 401.
- `fomo.family` renders no public trader data and is not a viable crawl target.
- Mock data remains fictional and is never used as a live fallback.
- FOMO Labs internal backend and WebSocket remain unsupported.

## Remaining Blockers

1. The crawl covers about 10 traders per window and carries no per-trade history, so
   trade-derived score components stay empty and confidence stays low. `FOMO_API_KEY`
   with `DATA_PROVIDER=fomoapi` is required for detail, trade, and balance analytics.
2. Volume and follower counts are read at the page's rendered precision (`$1.2M`, `3.4K`).
3. Crawl mode depends on a local Chromium; deployments need `playwright install chromium`.
4. The crawl is coupled to the page's DOM structure and breaks visibly, not silently,
   if that markup changes.
5. PostgreSQL and Redis need deployment credentials and operational health monitoring.
6. Telegram credentials are required only to enable notifications.

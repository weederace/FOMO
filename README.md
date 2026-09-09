# FOMO Whale Intelligence

An analytics-only platform for discovering and monitoring traders from legitimately
accessible public data. It does not trade, bypass access controls, or collect credentials.

## Run on Windows

Double-click `run.bat`. On the first run it creates `.venv`, installs the
dependencies and Chromium, writes `.env` from `.env.example`, and then opens the
launcher window; later runs skip straight to the window.

The launcher is Tkinter only, so it adds no dependency. Its left pane scrolls through
every action — research, collect, worker, API, seed, migrations, score recalculation,
the site-discovery crawlers, tests, lint, and dependency repair — and streams each
one's output into the log pane. Long-running actions such as the API and the worker
keep running until you press Stop.

**Start research** is the first action. One page render reads every public widget —
the leaderboard for `24h`, `7d`, `30d` and all time, the live alert stream, and the
trade theses — and writes a plain-text report to `reports/research-<timestamp>.txt`
with a leaderboard table per window, a per-trader index with wallets, the alert feed,
and each thesis with the position behind it. The next two actions open the newest
report and the reports folder. Research does not write to the database; `collect`
does that.

Chromium installs itself on demand: `run.bat` verifies it on every start, and if the
provider still finds it missing at runtime it downloads it and retries once.

`run.bat` also takes a command, which is handy from a terminal or a shortcut:

```text
run.bat research | collect | api | worker | check | test | seed | reinstall
run.bat menu
```

## Run manually

```text
python -m venv .venv
pip install -e ".[dev]"
playwright install chromium
python scripts/collect_once.py
uvicorn app.api.main:app --reload
```

The default provider is `crawl`, which needs no API key but does need a local Chromium,
so `playwright install chromium` is part of setup. Use `DATA_PROVIDER=mock` plus
`python scripts/seed_mock_data.py` for fictional data with no browser and no network.

Run tests with `pytest`; lint with `ruff check .`. Copy `.env.example` to `.env` to
configure PostgreSQL, Redis, collection intervals, thresholds, and optional Telegram.
The default SQLite database requires no external services.

### Your own API keys

The repo ships **no keys**. Every integration reads its key from your local `.env`
(copy `.env.example` → `.env`); the top of that file lists where to obtain each one:

| Key | Used for | Required? |
|---|---|---|
| `GMGN_API_KEY` | wallet enrichment, GMGN Radar tab, market alerts | optional (free at gmgn.ai/ai) |
| `COINGECKO_API_KEY` | token prices in Token Rankings / Movers | optional (free demo plan) |
| `ETHERSCAN_API_KEY` | on-chain EVM transfer scans (ETH/Base/BSC) | optional (free) |
| `CRYPTOAPIS_API_KEY` | faster on-chain source with Etherscan fallback | optional |
| `SOLSCAN_API_KEY` | Solana on-chain scans | optional |
| `FOMO_API_KEY` | only when `DATA_PROVIDER=fomoapi` | optional (default `crawl` needs none) |

Missing keys disable only their own feature — the rest of the app keeps working.
If you cloned this repo from GitHub, also check `.zcode/skills/` for optional
agent-skill definitions; they are documentation, not credentials.

### Optional On-chain Wallet Scanning

Use `API settings` in the launcher to save an Etherscan API key (shared by Ethereum,
Base, and BSC) and a Solscan API key in `.env`. Set `CHAIN_SCAN_ENABLED=true` to scan wallets discovered by the
leaderboard. EVM scans record ERC-20 transfers as `RECEIVED` or `SENT`; they do not
claim a transfer is a DEX buy or sell without a protocol decoder. Requests are
throttled to 5 per second and 100,000 per rolling day for EVM, and 1,000 per second
and 10,000,000 per rolling month for Solana. Wallet scans are bounded and run every
60 seconds by default, independently of the 10-second leaderboard worker.

## Live dashboard

The desktop launcher now opens a dark, tabbed dashboard. It starts the local API in
the background and refreshes `/dashboard` every five seconds. The dashboard includes
a sortable leaderboard, wallet copy on double-click, PnL deltas, KPI cards, alert
summaries, and a separate System Logs tab. Token-flow panels stay hidden in crawl
mode because the public page does not expose a reliable realtime trade stream. The API also exposes the raw dashboard
payload at `http://127.0.0.1:8000/dashboard`.

The live analytics score uses consistency (35%), capital efficiency (35%), strategy
stability (15%), and social proof (15%). Crawl mode can score leaderboard snapshots,
but it cannot show private trade history or balances; those panels remain empty until
the selected provider supplies trade data. `fomoapi` requires a valid API key.

Continuous worker:

```text
python scripts/run_worker.py
```

Operational commands include `python scripts/check_provider.py`,
`python scripts/recalculate_scores.py`, and `python scripts/research.py`
(`--windows 24h 7d`, `--output path.txt`). Run `alembic upgrade head` before PostgreSQL
deployment. Docker Compose starts the API, worker, PostgreSQL, and Redis; the image
installs Chromium so crawl mode works in a container.

## Provider selection

| `DATA_PROVIDER` | Source | Key | Leaderboard | Alerts | Trades / balances |
| --- | --- | --- | --- | --- | --- |
| `crawl` (default) | Public page rendered with Playwright | None | Yes | Yes | No |
| `fomoapi` | `api.fomoapi.io` over HTTP | `FOMO_API_KEY` | Yes | Yes | Yes |
| `mock` | In-process fictional data | None | Yes | No | Yes |

`crawl` renders `CRAWL_URL` (default `https://fomoapi.io/`) in a headless browser and
reads only what a visitor sees: the live leaderboard table and the alert stream. It
sends no API key, reads no cookies, and honours the target host's `robots.txt` before
the first render. `browser` and `fomo-crawl` are accepted aliases for `crawl`.

Its limits are real and reported honestly through `/provider/capabilities`: one time
window per run (`CRAWL_LEADERBOARD_WINDOW`, one of `24h`, `7d`, `30d`, `all`), roughly
ten rows per window, and no per-trader trade or balance history. Volume and follower
counts are rendered compactly (`$1.2M`, `3.4K`) and are parsed at that precision; PnL,
trade counts, ranks, and full Solana/EVM wallet addresses are exact. The page is
re-rendered at most once per `CRAWL_CACHE_SECONDS`. If nothing renders, collection
fails loudly and never silently falls back to mock or API data.

`fomoapi` remains available for per-trader profiles, trades, and balances, which the
public page does not show. It is opt-in: set `DATA_PROVIDER=fomoapi` and `FOMO_API_KEY`.
Keyless access to that API returned HTTP 401 on 2026-09-07, so a key is now required
for every endpoint including the leaderboard.

`fomo.family` itself is not a usable crawl target: its leaderboard, feed, and profile
routes render nothing without a login, and its `robots.txt` disallows `/profile/`,
`/user/`, and `/u/`. See `docs/public_crawl_report.md`.

## API

`/health`, `/traders`, `/traders/{id}`, `/traders/top`, `/traders/emerging`,
`/traders/rankings`, `/traders/{id}/history`, `/traders/{id}/trades`,
`/traders/{id}/score`, `/traders/{id}/score-history`, `/leaderboard`, `/alerts`,
`/stats`, `/provider/status`, and `/provider/capabilities` are available from FastAPI.

## Scoring

The Smart Whale Score uses configurable available components: consistency 25%, win rate
15%, risk-adjusted 20%, early entry 15%, trade quality 15%, activity 10%. Missing data is
excluded and confidence reports sample size, history, completeness, and freshness. Under
`crawl` the trade-derived components are absent, so confidence stays correspondingly low.

## Security

Secrets are environment-only and sanitized from discovery output. Mock data is fictional.
Live collection remains limited to public, permitted data.

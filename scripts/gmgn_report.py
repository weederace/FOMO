"""Pull the GMGN market feeds and whale intel into a readable text report.

Read-only: everything comes from the gmgn-cli tool and the local database;
nothing is written back to either. The report lands under `reports/` next to
the crawl research reports.
"""

import argparse
import asyncio
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings
from app.providers.gmgn import GmgnClient, GmgnProviderError, cli_available, describe_setup
from app.services.gmgn_market import _token_row
from app.services.gmgn_service import discover_wallets, wallet_score
from app.utils.logging import configure_logging

WIDTH = 96
RULE = "=" * WIDTH
THIN = "-" * WIDTH


def money(value: object, decimals: int = 0) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def width_of(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(character) in "WF" else 1 for character in text)


def clip(value: object, width: int) -> str:
    text = str(value if value not in (None, "") else "-")
    if width_of(text) <= width:
        return text
    kept = ""
    for character in text:
        if width_of(kept + character) > width - 1:
            break
        kept += character
    return kept + "…"


def cell(value: object, width: int, right: bool = False) -> str:
    text = clip(value, width)
    padding = " " * max(0, width - width_of(text))
    return padding + text if right else text + padding


TOKEN_COLUMNS = (("SYMBOL", 12), ("CHAIN", 6), ("MCAP USD", 12), ("LIQ USD", 11),
                 ("VOL USD", 11), ("HOLDERS", 8), ("SMART", 6), ("ADDRESS", 34))


def token_table(title: str, rows: list[dict]) -> list[str]:
    lines = [title, THIN]
    if not rows:
        lines.append("  (no data — check GMGN_ENABLED and GMGN_API_KEY)")
        lines.append("")
        return lines
    header = "".join(
        cell(name, width, right=index > 1).replace(" ", " ", 1) for index, (name, width) in enumerate(TOKEN_COLUMNS)
    )
    lines.append(header.rstrip()[:WIDTH])
    for row in rows[:40]:
        line = "".join([
            cell(row.get("symbol") or "-", TOKEN_COLUMNS[0][1]),
            cell(row.get("chain") or "-", TOKEN_COLUMNS[1][1]),
            cell(money(row.get("market_cap_usd")), TOKEN_COLUMNS[2][1], right=True),
            cell(money(row.get("liquidity_usd")), TOKEN_COLUMNS[3][1], right=True),
            cell(money(row.get("volume_usd")), TOKEN_COLUMNS[4][1], right=True),
            cell(money(row.get("holders")), TOKEN_COLUMNS[5][1], right=True),
            cell(money(row.get("smart_degen_count")), TOKEN_COLUMNS[6][1], right=True),
            cell(row.get("address") or "-", TOKEN_COLUMNS[7][1]),
        ])
        lines.append(line.rstrip()[:WIDTH])
    lines.append("")
    return lines


async def wallet_block(client: GmgnClient, settings, limit: int = 15) -> list[str]:
    lines = ["WHALE WALLET INTELLIGENCE (top wallets discovered by the crawler)", RULE]
    from app.database.session import SessionLocal

    async with SessionLocal() as session:
        pairs = await discover_wallets(session, settings)
    if not pairs:
        lines += ["  (no wallets with addresses in the database yet)", ""]
        return lines
    chain = settings.gmgn_default_chain
    for trader_id, wallet in pairs[:limit]:
        try:
            stats = await client.portfolio_stats(chain, [wallet])
            if not stats:
                continue
            readout = wallet_score(stats[0])
            realized = readout.get("realized_profit_usd")
            lines.append(
                f"  trader #{trader_id}  {wallet[:14]}…{wallet[-6:]}  "
                f"score {readout['score']}  [{readout['verdict']}]"
            )
            lines.append(
                f"    realized ${money(realized)}  win rate {money(readout.get('win_rate'), 1)}%  "
                f"multiplier {money(readout.get('pnl_multiplier'), 2)}x  "
                f"buys {readout.get('buy_count') or 0}  sells {readout.get('sell_count') or 0}  "
                f"style: {readout.get('style')}"
            )
        except GmgnProviderError as exc:
            lines.append(f"  trader #{trader_id}  {wallet[:14]}…  unavailable: {exc}")
    lines.append("")
    return lines


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_token_rows(rows: list[dict], chain: str) -> list[dict]:
    return [_token_row(chain, row) for row in rows]


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup = describe_setup(settings.gmgn_cli_command)
    captured = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "GMGN MARKET & WALLET INTELLIGENCE REPORT",
        RULE,
        f"captured: {captured}",
        f"cli: {setup['cli_command']}  found: {setup['cli_found']}  "
        f"api key set: {setup['api_key_set']}  enabled: {settings.gmgn_enabled}",
        RULE,
        "",
    ]

    if not await cli_available(settings.gmgn_cli_command):
        lines.append("gmgn-cli is not installed yet. It installs itself on first use,")
        lines.append("or run: npm install -g gmgn-cli   (requires Node.js)")
        output = args.output or Path("reports") / f"gmgn-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines), encoding="utf-8-sig")
        print(f"gmgn-cli missing; stub report written to {output.resolve()}")
        return 1

    client = GmgnClient(
        command=settings.gmgn_cli_command,
        request_timeout_seconds=settings.gmgn_request_timeout_seconds,
    )
    chain = settings.gmgn_default_chain
    limit = settings.gmgn_market_row_limit
    failures = 0
    try:
        print(f"Fetching trending tokens on {chain} (5m) ...")
        try:
            trending = _to_token_rows(await client.market_trending(chain, "5m", limit=limit), chain)
        except GmgnProviderError as exc:
            trending, failures = [], failures + 1
            lines.append(f"trending unavailable: {exc}")
        lines.extend(token_table(f"TOP TRENDING TOKENS ON {chain.upper()} - LAST 5 MINUTES", trending))

        print("Fetching trenches (new / near graduation / completed) ...")
        try:
            buckets = await client.market_trenches(chain, limit=limit)
        except GmgnProviderError as exc:
            buckets, failures = {}, failures + 1
            lines.append(f"trenches unavailable: {exc}")
        for kind, title in (
            ("new_creation", "NEWLY CREATED TOKENS"),
            ("near_completion", "NEAR GRADUATION (bonding curve almost full)"),
            ("completed", "RECENTLY MIGRATED TO DEX"),
        ):
            rows = _to_token_rows(buckets.get(kind, []), chain)
            lines.extend(token_table(f"{title} ON {chain.upper()}", rows))

        print("Fetching hot searches (5m) ...")
        try:
            hot = _to_token_rows(await client.market_hot_searches("5m", limit=limit), chain)
        except GmgnProviderError as exc:
            hot, failures = [], failures + 1
            lines.append(f"hot searches unavailable: {exc}")
        lines.extend(token_table("MOST-SEARCHED TOKENS - LAST 5 MINUTES", hot))

        if not args.skip_wallets:
            print("Reading whale wallet intelligence ...")
            lines.extend(await wallet_block(client, settings, limit=10))
    finally:
        await client.aclose()

    lines += [RULE, f" END OF REPORT  -  {captured}  -  {failures} feed failure(s)", RULE, ""]
    output = args.output or Path("reports") / f"gmgn-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8-sig")
    print(f"Trending {len(trending)}  Trenches {sum(len(v) for v in buckets.values())}  Hot {len(hot)}")
    print(f"Report   {output.resolve()}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a GMGN market + wallet intel text report.")
    parser.add_argument("--output", type=Path, default=None,
                        help="Report path; defaults to reports/gmgn-<timestamp>.txt")
    parser.add_argument("--skip-wallets", action="store_true",
                        help="Skip the per-wallet portfolio section (faster).")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

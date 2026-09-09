"""Scan every public widget on the crawl source and write a readable report.

One page render collects the leaderboard for each window, the live alert stream, and
the thesis stream, then writes a plain-text file under `reports/`. It reads only what
a visitor sees and never touches the database; `scripts/collect_once.py` does that.
"""

import argparse
import asyncio
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings
from app.providers.crawl import LEADERBOARD_WINDOWS, CrawlProvider, SiteScan
from app.utils.logging import configure_logging

WIDTH = 96
RULE = "=" * WIDTH
THIN = "-" * WIDTH
WINDOW_LABEL = {"24h": "last 24 hours", "7d": "last 7 days", "30d": "last 30 days", "all": "all time"}


def money(value: object, decimals: int = 0) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.{decimals}f}"


def width_of(text: str) -> int:
    """Columns a string occupies; CJK handles and emoji take two."""
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


COLUMNS = (("RANK", 4), ("HANDLE", 20), ("DISPLAY NAME", 22), ("PNL USD", 13),
           ("VOLUME USD", 13), ("TRADES", 7), ("FOLLOWERS", 10))


def leaderboard_block(window: str, traders: list) -> list[str]:
    header = "  ".join(cell(title, size, right=index != 1 and index != 2)
                       for index, (title, size) in enumerate(COLUMNS))
    lines = [
        "",
        RULE,
        f" LEADERBOARD  {window}  ({WINDOW_LABEL.get(window, window)})  -  {len(traders)} traders",
        RULE,
        f" {header}",
        " " + "  ".join("-" * size for _title, size in COLUMNS),
    ]
    for trader in traders:
        values = (
            cell(trader.rank or "-", 4, right=True),
            cell(trader.username, 20),
            cell(trader.display_name, 22),
            cell(money(trader.pnl), 13, right=True),
            cell(money(trader.volume), 13, right=True),
            cell(money(trader.total_trades), 7, right=True),
            cell(money(trader.follower_count), 10, right=True),
        )
        lines.append(" " + "  ".join(values))
    return lines


def trader_index_block(scan: SiteScan) -> list[str]:
    traders = scan.unique_traders()
    placements = scan.placements()
    lines = ["", RULE, f" TRADER INDEX  -  {len(traders)} unique handles", RULE]
    order = sorted(traders.values(), key=lambda item: float(item.pnl or 0), reverse=True)
    for trader in order:
        seen = ", ".join(
            f"{window} #{rank}" if rank else window for window, rank in placements[trader.platform_trader_id]
        )
        lines.extend([
            "",
            f" @{trader.username}",
            f"   display name   {trader.display_name or '-'}",
            f"   appears in     {seen}",
            f"   best pnl       {money(trader.pnl)} USD",
            f"   volume         {money(trader.volume)} USD",
            f"   trades         {money(trader.total_trades)}",
            f"   followers      {money(trader.follower_count)}",
            f"   wallet         {trader.wallet_address or 'not resolved on the page'}",
            f"   profile        {trader.profile_url}",
        ])
    return lines


def alerts_block(scan: SiteScan) -> list[str]:
    lines = ["", RULE, f" LIVE ALERTS  -  {len(scan.alerts)} events", RULE]
    if not scan.alerts:
        lines.append(" The alert stream rendered nothing during this scan.")
        return lines
    lines.extend([
        f" {cell('AGE', 5, right=True)}  {cell('TYPE', 6)}  {cell('USD', 11, right=True)}  EVENT",
        f" {'-' * 5}  {'-' * 6}  {'-' * 11}  {'-' * 60}",
    ])
    for alert in scan.alerts:
        lines.append(
            f" {cell(alert.get('age'), 5, right=True)}  {cell(alert.get('type'), 6)}  "
            f"{cell(money(alert.get('usdValue')), 11, right=True)}  {alert.get('text', '')}"
        )
    return lines


def theses_block(scan: SiteScan) -> list[str]:
    lines = ["", RULE, f" TRADE THESES  -  {len(scan.theses)} entries", RULE]
    if not scan.theses:
        lines.append(" The thesis stream rendered nothing during this scan.")
        return lines
    for thesis in scan.theses:
        position = " / ".join(
            f"{label} {money(thesis[key])} USD"
            for label, key in (("holding", "holdingUsd"), ("unrealised", "unrealizedPnlUsd"), ("realised", "realizedPnlUsd"))
            if thesis.get(key) is not None
        )
        lines.extend([
            "",
            f" @{thesis['handle']}  on  ${thesis.get('token') or '?'}",
            f'   "{thesis["text"]}"',
            f"   {position or 'no position figures were shown'}",
        ])
    return lines


def render_report(scan: SiteScan) -> str:
    captured = scan.captured_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        RULE,
        " FOMO WHALE INTELLIGENCE  -  PUBLIC SITE RESEARCH",
        RULE,
        f" Source      {scan.url}",
        f" Captured    {captured}",
        f" Windows     {', '.join(scan.windows)}",
        f" Traders     {len(scan.unique_traders())} unique across {len(scan.windows)} windows",
        f" Alerts      {len(scan.alerts)}",
        f" Theses      {len(scan.theses)}",
        "",
        THIN,
        " HOW TO READ THIS",
        THIN,
        " Everything below was read from the rendered public page, with no API key and no",
        " login. Ranks, PnL, trade counts and wallet addresses are exact. Volume and",
        " follower counts are displayed compactly on the page ($1.2M, 3.4K) and are",
        " recorded at that precision, so treat them as approximate.",
        " Per-trade history and balances are not shown publicly and are absent here.",
    ]
    for window, traders in scan.windows.items():
        lines.extend(leaderboard_block(window, traders))
    lines.extend(trader_index_block(scan))
    lines.extend(alerts_block(scan))
    lines.extend(theses_block(scan))
    lines.extend(["", RULE, f" END OF REPORT  -  {captured}", RULE, ""])
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    provider = CrawlProvider(
        settings.crawl_url,
        settings.crawl_leaderboard_window,
        settings.crawl_leaderboard_limit,
        settings.browser_executable_path,
        settings.browser_headless,
        settings.crawl_render_timeout_seconds,
        settings.crawl_cache_seconds,
        settings.crawl_alert_limit,
        settings.crawl_respect_robots,
    )
    try:
        print(f"Scanning {settings.crawl_url} across {', '.join(args.windows)} ...")
        scan = await provider.scan(tuple(args.windows))
    finally:
        await provider.aclose()

    output = args.output or Path("reports") / f"research-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Notepad and Excel open handles and theses without mojibake.
    output.write_text(render_report(scan), encoding="utf-8-sig")

    print(f"Traders  {len(scan.unique_traders())} unique across {len(scan.windows)} windows")
    print(f"Alerts   {len(scan.alerts)}")
    print(f"Theses   {len(scan.theses)}")
    print(f"Report   {output.resolve()}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan the public crawl source into a text report.")
    parser.add_argument("--windows", nargs="+", default=list(LEADERBOARD_WINDOWS),
                        choices=list(LEADERBOARD_WINDOWS), help="Leaderboard windows to read.")
    parser.add_argument("--output", type=Path, default=None, help="Report path; defaults to reports/research-<timestamp>.txt")
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

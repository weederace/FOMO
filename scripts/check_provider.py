import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings
from app.providers.crawl import LEADERBOARD_WINDOWS, CrawlProviderError
from app.providers.factory import create_provider
from app.providers.fomo import FomoProviderError
from app.utils.logging import configure_logging


async def main() -> None:
    settings = get_settings()
    provider = create_provider(settings)
    try:
        try:
            if provider.name == "fomo-crawl":
                scan = await provider.scan(LEADERBOARD_WINDOWS)
                rows = scan.unique_traders()
                result = {
                    "provider": provider.name,
                    "status": "ok",
                    "capabilities": provider.capabilities(),
                    "leaderboard_rows": len(rows),
                    "windows": {window: len(items) for window, items in scan.windows.items()},
                    "alerts": len(scan.alerts),
                    "theses": len(scan.theses),
                }
            else:
                rows = await provider.get_leaderboard(limit=100)
                result = {
                    "provider": provider.name,
                    "status": "ok",
                    "capabilities": provider.capabilities(),
                    "leaderboard_rows": len(rows),
                }
        except (CrawlProviderError, FomoProviderError) as exc:
            print({"provider": provider.name, "status": "unavailable", "reason": str(exc)})
            return
        print(result)
    finally:
        close = getattr(provider, "aclose", None)
        if close:
            await close()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())

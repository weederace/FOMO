"""Verify the GMGN toolchain: CLI presence (installs if missing) and API key.

Read-only except for the one-time `npm install -g gmgn-cli` when the CLI is
absent. Never calls any GMGN data endpoint, so it works without a key.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings
from app.providers.gmgn import GmgnProviderError, describe_setup, install_cli
from app.utils.logging import configure_logging


async def run() -> int:
    settings = get_settings()
    setup = describe_setup(settings.gmgn_cli_command)
    print(f"GMGN_CLI_COMMAND : {setup['cli_command']}")
    print(f"CLI on PATH      : {'yes' if setup['cli_found'] else 'no'}")
    print(f"GMGN_API_KEY set : {'yes' if setup['api_key_set'] else 'no'}")
    print(f"GMGN_ENABLED     : {settings.gmgn_enabled}")
    if not setup["api_key_set"]:
        print("\nNo API key. Get a free one at https://gmgn.ai/ai, then set")
        print("GMGN_API_KEY in .env or through the launcher's API settings dialog.")

    if not setup["cli_found"]:
        print("\ngmgn-cli is missing. Installing it through npm (this happens once) ...")
        try:
            await install_cli()
        except GmgnProviderError as exc:
            print(f"install failed: {exc}")
            return 1
        print("gmgn-cli installed.")

    print("\nGMGN toolchain is ready." if setup["api_key_set"] else
          "\ngmgn-cli is ready; add GMGN_API_KEY to start collecting data.")
    return 0


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    raise SystemExit(asyncio.run(run()))

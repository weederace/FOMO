import pytest

from app.monitoring.deduplication import Deduplicator
from app.telegram.formatter import format_alert


@pytest.mark.asyncio
async def test_deduplication_claims_once():
    dedup = Deduplicator()
    assert await dedup.claim("same")
    assert not await dedup.claim("same")


def test_formatter_omits_missing_values():
    message = format_alert("NEW SMART WHALE", "alice", 92.4, 89, "ELITE WHALE", 18)
    assert "alice" in message and "#18" in message

import pytest

from millicall.ai.tts.cache import PromptCache


class _FakeTTS:
    def __init__(self):
        self.calls = 0

    async def synthesize(self, text):
        self.calls += 1
        return b"\x00\x00" * 80


@pytest.mark.asyncio
async def test_cache_miss_then_hit(tmp_path):
    cache = PromptCache(tmp_path)
    tts = _FakeTTS()
    p1 = await cache.get_or_synth("k1", tts, "こんにちは")
    assert p1.exists()
    assert tts.calls == 1
    p2 = await cache.get_or_synth("k1", tts, "こんにちは")
    assert p2 == p1
    assert tts.calls == 1  # 2回目は合成しない

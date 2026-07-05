from pathlib import Path

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


@pytest.mark.asyncio
async def test_get_or_synth_writes_via_tmp_and_no_tmp_remains(tmp_path, monkeypatch):
    """書き込みが一時ファイル(.tmp)経由で行われ、成功後に .tmp が残らないことを確認する。

    write_bytes が呼ばれたパスをキャプチャし、.tmp サフィックスに対してのみ
    書き込まれることを検証する（直接 .wav へ書き込む非アトミック実装を検出できる）。
    """
    written_paths: list[Path] = []
    original_write_bytes = Path.write_bytes

    def capturing_write_bytes(self: Path, data: bytes) -> int:
        written_paths.append(self)
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", capturing_write_bytes)

    cache = PromptCache(tmp_path)
    tts = _FakeTTS()
    path = await cache.get_or_synth("atomic_test", tts, "テスト")

    # 本パスが存在し、有効な WAV であること
    assert path.exists()
    wav_bytes = path.read_bytes()
    assert wav_bytes[:4] == b"RIFF", "本パスに正しい WAV が書かれていること"

    # write_bytes は .tmp パスに対してのみ呼ばれるべき（直接 .wav へ書かない）
    assert len(written_paths) >= 1, "write_bytes が一度も呼ばれなかった"
    non_tmp = [p for p in written_paths if p.suffix != ".tmp"]
    assert non_tmp == [], (
        f"write_bytes が .tmp 以外のパスに呼ばれた（非アトミック書き込みを検出）: {non_tmp}"
    )

    # 成功後に同一ディレクトリへ .tmp ファイルが残っていないこと
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f".tmp ファイルが残存: {tmp_files}"

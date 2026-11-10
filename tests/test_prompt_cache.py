import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from millicall.ai.tts.cache import PromptCache, tts_cache_key


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


# --- issue #80: キー正規化・プロセス内の生成共有・一意な一時ファイル ---


class _GatedTTS:
    """合成を Event で止められる Fake。fail_times 回目までの呼び出しは失敗させる。"""

    def __init__(self, fail_times: int = 0):
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._fail_times = fail_times

    async def synthesize(self, text):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.calls <= self._fail_times:
            raise RuntimeError("synth failed")
        return b"\x01\x00" * 80


def test_key_changes_when_voice_settings_change():
    vv = {"engine_url": "http://10.0.0.5:50021", "speaker": 1}
    base = tts_cache_key("voicevox", vv, "案内")
    assert base != tts_cache_key("voicevox", {**vv, "speaker": 2}, "案内")
    assert base != tts_cache_key("voicevox", {**vv, "engine_url": "http://10.0.0.6:50021"}, "案内")
    assert base != tts_cache_key("voicevox", vv, "別文")
    # kind が違えば同じ設定・テキストでも別キー
    assert tts_cache_key("openjtalk", {}, "案内") != tts_cache_key("coefont", {}, "案内")
    cf = {"coefont": "abc", "speed": 1.0, "pitch": 0.0}
    base_cf = tts_cache_key("coefont", cf, "x")
    assert base_cf != tts_cache_key("coefont", {**cf, "speed": 1.2}, "x")
    assert base_cf != tts_cache_key("coefont", {**cf, "pitch": 50}, "x")
    assert base_cf != tts_cache_key("coefont", {**cf, "coefont": "z"}, "x")
    assert tts_cache_key("openjtalk", {"voice_path": "/v/a.htsvoice"}, "x") != tts_cache_key(
        "openjtalk", {"voice_path": "/v/b.htsvoice"}, "x"
    )
    assert tts_cache_key("openjtalk", {"dict_dir": "/d/a"}, "x") != tts_cache_key(
        "openjtalk", {"dict_dir": "/d/b"}, "x"
    )


def test_key_normalizes_defaults_and_ignores_non_voice_fields():
    # 既定値の明示/省略・数値型の揺れ・キー順は同一キー
    assert tts_cache_key("voicevox", {}, "x") == tts_cache_key(
        "voicevox", {"speaker": 1.0, "engine_url": "http://127.0.0.1:50021"}, "x"
    )
    assert tts_cache_key("coefont", {"coefont": "a", "speed": 1}, "x") == tts_cache_key(
        "coefont", {"speed": 1.0, "coefont": "a"}, "x"
    )
    # 認証用の access_key や無関係な項目は音声に影響しないのでキーに含めない
    assert tts_cache_key("coefont", {"coefont": "a"}, "x") == tts_cache_key(
        "coefont", {"coefont": "a", "access_key": "AK-secret", "note": "memo"}, "x"
    )
    assert "AK-secret" not in tts_cache_key("coefont", {"access_key": "AK-secret"}, "x")


@pytest.mark.asyncio
async def test_concurrent_requests_share_single_synthesis(tmp_path):
    cache = PromptCache(tmp_path)
    tts = _GatedTTS()
    waiters = [asyncio.create_task(cache.get_or_synth("k", tts, "同時")) for _ in range(5)]
    await tts.started.wait()
    await asyncio.sleep(0)
    tts.release.set()
    paths = await asyncio.gather(*waiters)
    assert tts.calls == 1  # 合成は1回だけ
    assert len(set(paths)) == 1
    assert paths[0].read_bytes()[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_concurrent_requests_from_separate_cache_instances_share(tmp_path):
    # router はリクエスト毎に PromptCache を作るため、インスタンスを跨いで共有されること
    tts = _GatedTTS()
    waiters = [
        asyncio.create_task(PromptCache(tmp_path).get_or_synth("k", tts, "同時")) for _ in range(3)
    ]
    await tts.started.wait()
    await asyncio.sleep(0)
    tts.release.set()
    await asyncio.gather(*waiters)
    assert tts.calls == 1


@pytest.mark.asyncio
async def test_waiter_cancel_does_not_cancel_shared_synthesis(tmp_path):
    cache = PromptCache(tmp_path)
    tts = _GatedTTS()
    first = asyncio.create_task(cache.get_or_synth("k", tts, "共有"))
    second = asyncio.create_task(cache.get_or_synth("k", tts, "共有"))
    await tts.started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    tts.release.set()
    path = await second  # 生成本体は巻き添えにならず完了する
    assert path.exists()
    assert tts.calls == 1


@pytest.mark.asyncio
async def test_generation_completes_even_if_all_waiters_cancelled(tmp_path):
    cache = PromptCache(tmp_path)
    tts = _GatedTTS()
    only = asyncio.create_task(cache.get_or_synth("k", tts, "孤立"))
    await tts.started.wait()
    only.cancel()
    with pytest.raises(asyncio.CancelledError):
        await only
    tts.release.set()
    for _ in range(10):
        await asyncio.sleep(0)
    path = cache.path_for("k")
    assert path.exists()
    # 次の要求はキャッシュヒットで合成しない
    assert await cache.get_or_synth("k", tts, "孤立") == path
    assert tts.calls == 1


@pytest.mark.asyncio
async def test_failed_synthesis_is_not_cached_and_retried(tmp_path):
    cache = PromptCache(tmp_path)
    tts = _GatedTTS(fail_times=1)
    tts.release.set()
    a = asyncio.create_task(cache.get_or_synth("k", tts, "失敗"))
    b = asyncio.create_task(cache.get_or_synth("k", tts, "失敗"))
    results = await asyncio.gather(a, b, return_exceptions=True)
    # 同時の2要求は1回の失敗を共有する
    assert all(isinstance(r, RuntimeError) for r in results)
    assert tts.calls == 1
    assert not cache.path_for("k").exists()
    # 次の要求で再試行され成功する（失敗結果はキャッシュされない）
    path = await cache.get_or_synth("k", tts, "失敗")
    assert path.exists()
    assert tts.calls == 2


def test_write_atomic_uses_unique_tmp_and_cleans_up_on_failure(tmp_path, monkeypatch):
    replaced: list[Path] = []
    original_replace = os.replace

    def capturing_replace(src, dst):
        replaced.append(Path(src))
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", capturing_replace)
    cache = PromptCache(tmp_path)
    target = cache.path_for("k")
    cache.write_atomic(target, b"one")
    cache.write_atomic(target, b"two")
    assert replaced[0] != replaced[1]  # 書き込み毎に別の一時ファイル名
    assert all(p.suffix == ".tmp" and p.parent == target.parent for p in replaced)
    assert target.read_bytes() == b"two"

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        cache.write_atomic(target, b"three")
    assert target.read_bytes() == b"two"  # 失敗しても既存の完全な WAV は残る
    assert list(tmp_path.glob("*.tmp")) == []  # 一時ファイルは即削除


_WRITER_SCRIPT = """
import sys
from millicall.ai.tts.cache import PromptCache

cache = PromptCache(sys.argv[1])
data = bytes.fromhex(sys.argv[2]) * 200_000
path = cache.path_for("multi-process")
for _ in range(20):
    # 既存チェックを飛ばして毎回置き換え、プロセス間の書き込み競合を意図的に起こす
    cache.write_atomic(path, data)
"""


def test_concurrent_processes_never_expose_broken_wav(tmp_path):
    """複数プロセスが同一キーを同時に書いても、読み手は完全なファイルしか見ない。"""
    unit = b"RIFF"
    expected = unit * 200_000
    target = PromptCache(tmp_path).path_for("multi-process")
    procs = [
        subprocess.Popen([sys.executable, "-c", _WRITER_SCRIPT, str(tmp_path), unit.hex()])
        for _ in range(4)
    ]
    problems: list[str] = []
    deadline = time.monotonic() + 60
    while any(p.poll() is None for p in procs) and time.monotonic() < deadline:
        if not target.exists():
            continue
        try:
            data = target.read_bytes()
        except FileNotFoundError:
            problems.append("FileNotFoundError")
            continue
        if data != expected:
            problems.append(f"broken file len={len(data)}")
    codes = [p.wait(timeout=60) for p in procs]
    assert codes == [0, 0, 0, 0]
    assert problems == []
    assert target.read_bytes() == expected
    assert list(tmp_path.glob("*.tmp")) == []


def test_sweep_removes_only_stale_tmp(tmp_path):
    stale = tmp_path / "old.123.abc.tmp"
    fresh = tmp_path / "new.456.def.tmp"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    PromptCache(tmp_path).sweep_stale_tmp()
    assert not stale.exists()
    assert fresh.exists()  # 他プロセスが生成中かもしれない新しい一時ファイルは残す

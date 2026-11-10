"""定型文 TTS の永続キャッシュ（内容アドレス型、prompts/ 配下）。

キー: ``tts_cache_key(kind, config, text)`` に一本化する。kind と「音声に影響する設定」の
正規化ハッシュを含めるため、speaker・速度などを変えると別キーになり新しい音声が生成される。
API キー等の秘密値（api_key 引数側）や音声に影響しない値（coefont の access_key 等）は含めない。

並行性:
  * プロセス内: 同一パスの生成中タスクを共有し、合成の重複を防ぐ。待機側は shield 越しに
    待つため、待機側がキャンセルされても生成本体は止まらない。失敗した生成は結果を残さず
    in-flight 表から外すので、次の要求で再試行される。
  * プロセス間: ロックは取らない（同一キーを複数プロセスが同時に合成し得るが結果は同一）。
    一時ファイル名を pid + uuid で一意にし os.replace で置き換えるため、他プロセスに
    書き込み途中の WAV や消えた一時ファイル（FileNotFoundError）を見せない。
  * 残留一時ファイル: 書き込み失敗時は即削除。プロセス異常終了で残ったものは、
    ディレクトリごとにプロセス内で初回利用時に一度だけ、十分古い（_STALE_TMP_SECONDS 超）
    ``*.tmp`` を削除する（生成中の他プロセスの一時ファイルを消さないよう経過時間で判定）。

旧キー形式（``{provider_id}:{text}``）で作られた WAV は削除しない。ファイル名はどちらも
sha256 で旧新を区別できず、また返却パスをワークフロー等に転記して参照している可能性があるため。
"""

import asyncio
import contextlib
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from millicall.ai.audio import pcm8k_to_wav

_KEY_VERSION = "v2"
_STALE_TMP_SECONDS = 3600.0

# kind ごとの「音声に影響する設定」とその既定値（registry.build_tts の既定値と揃える）。
# 既定値を補ってから正規化するので、未指定と既定値の明示指定は同じキーになる。
_VOICE_FIELDS: dict[str, dict[str, object]] = {
    # engine_url: エンジン（VOICEVOX / AivisSpeech 等）が違えば同じ speaker ID でも別の声
    "voicevox": {"engine_url": "http://127.0.0.1:50021", "speaker": 1},
    "openjtalk": {
        "dict_dir": "/var/lib/mecab/dic/open-jtalk/naist-jdic",
        "voice_path": ("/usr/share/hts-voice/nitech-jp-atr503-m001/nitech_jp_atr503_m001.htsvoice"),
    },
    # access_key は認証用で音声に影響しないため含めない
    "coefont": {"coefont": "", "speed": 1.0, "pitch": 0.0},
}

# 生成中タスク（プロセス内共有）。キーは WAV の絶対パス。
_inflight: dict[Path, asyncio.Task[Path]] = {}
# 残留一時ファイルの掃除を済ませたディレクトリ
_swept_dirs: set[Path] = set()


def _normalize(value: object) -> object:
    # 数値は 1 と 1.0 を同一視する（bool は int のサブクラスなので除外）
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        return value.strip()
    return value


def voice_settings_hash(kind: str, config: dict) -> str:
    """音声に影響する設定だけを正規化して sha256 化する。"""
    fields = _VOICE_FIELDS.get(kind)
    if fields is None:
        # 未知の kind: どの項目が効くか分からないため config 全体を対象にする（安全側）
        settings = {k: _normalize(v) for k, v in config.items()}
    else:
        settings = {name: _normalize(config.get(name, default)) for name, default in fields.items()}
    canonical = json.dumps(settings, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def tts_cache_key(kind: str, config: dict, text: str) -> str:
    return f"{_KEY_VERSION}:{kind}:{voice_settings_hash(kind, config)}:{text}"


class PromptCache:
    def __init__(self, cache_dir) -> None:
        self._dir = Path(cache_dir).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)
        if self._dir not in _swept_dirs:
            _swept_dirs.add(self._dir)
            self.sweep_stale_tmp()

    def path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._dir / f"{digest}.wav"

    def sweep_stale_tmp(self, max_age: float = _STALE_TMP_SECONDS) -> None:
        """異常終了等で残った古い一時ファイルを削除する（他プロセスの生成中は消さない）。"""
        cutoff = time.time() - max_age
        for tmp in self._dir.glob("*.tmp"):
            with contextlib.suppress(OSError):
                if tmp.stat().st_mtime < cutoff:
                    tmp.unlink()

    async def get_or_synth(self, key: str, tts, text: str) -> Path:
        path = self.path_for(key)
        if path.exists():
            return path
        loop = asyncio.get_running_loop()
        task = _inflight.get(path)
        if task is None or task.get_loop() is not loop:
            task = loop.create_task(self._synth(path, tts, text))
            _inflight[path] = task
            task.add_done_callback(lambda t: _on_done(path, t))
        # shield: 待機側のキャンセルで生成本体（他の待機者と共有）を止めない
        return await asyncio.shield(task)

    async def _synth(self, path: Path, tts, text: str) -> Path:
        if path.exists():
            return path
        pcm = await tts.synthesize(text)
        self.write_atomic(path, pcm8k_to_wav(pcm))
        return path

    @staticmethod
    def write_atomic(path: Path, data: bytes) -> None:
        # 一意な一時ファイルに書いてから os.replace でアトミックに置き換える
        # （書き込み途中の終了やプロセス間の同時生成で壊れた WAV を見せないため）
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise


def _on_done(path: Path, task: asyncio.Task[Path]) -> None:
    # 失敗・成功を問わず表から外す（失敗結果はキャッシュせず次の要求で再試行させる）
    if _inflight.get(path) is task:
        del _inflight[path]
    if not task.cancelled():
        # 待機者が全員キャンセル済みでも "exception was never retrieved" を出さない
        task.exception()

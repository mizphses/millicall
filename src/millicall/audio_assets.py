"""組み込み音声アセット（パッケージ同梱の WAV）の一覧と、共有 tts ディレクトリへの同期。

同梱 WAV は ``millicall/assets/audio/`` にあり、``manifest.json`` に
``{"id", "label", "file"}`` の配列で登録する。core 起動時に
``{tts_cache_dir}/builtin/`` へコピーし、FreeSWITCH と共有している tts
ディレクトリ配下の絶対パスとして play_audio などから再生できるようにする。

ファイル名は ``is_safe_audio_path`` を満たすよう ASCII のみとし、日本語は label に置く。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

from millicall.config import get_settings

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable
    from pathlib import Path

BUILTIN_SUBDIR = "builtin"
FAX_RECEIVE_NOTICE_ID = "fax_receive_notice"


@dataclass(frozen=True)
class BuiltinAudio:
    id: str
    label: str
    file: str


def _asset_dir() -> Traversable:
    return resources.files("millicall.assets").joinpath("audio")


def list_builtin_audio() -> list[BuiltinAudio]:
    """manifest.json に登録された組み込み音声を返す。"""
    raw = json.loads(_asset_dir().joinpath("manifest.json").read_text(encoding="utf-8"))
    return [BuiltinAudio(id=e["id"], label=e["label"], file=e["file"]) for e in raw]


def builtin_audio_dir(tts_cache_dir: Path | None = None) -> Path:
    base = tts_cache_dir if tts_cache_dir is not None else get_settings().tts_cache_dir
    return base / BUILTIN_SUBDIR


def builtin_audio_path(audio_id: str, tts_cache_dir: Path | None = None) -> Path:
    """組み込み音声 ``audio_id`` の同期先絶対パス（play_audio の file_path に使える）。

    ``tts_cache_dir`` 省略時は ``get_settings().tts_cache_dir`` を使う。
    未登録の id は KeyError。
    """
    for a in list_builtin_audio():
        if a.id == audio_id:
            return builtin_audio_dir(tts_cache_dir) / a.file
    raise KeyError(audio_id)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sync_builtin_audio(dest_dir: Path) -> list[Path]:
    """同梱音声を ``dest_dir / "builtin"`` へコピーする（冪等）。

    宛先が無い、またはサイズ/sha256 が異なるときだけ書き込む（一時ファイル経由で置換）。
    実際にコピーしたファイルのパスを返す。
    """
    out_dir = builtin_audio_dir(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    src_root = _asset_dir()
    for a in list_builtin_audio():
        data = src_root.joinpath(a.file).read_bytes()
        dst = out_dir / a.file
        if (
            dst.is_file()
            and dst.stat().st_size == len(data)
            and _sha256(dst.read_bytes()) == _sha256(data)
        ):
            continue
        tmp = dst.with_name(dst.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dst)
        copied.append(dst)
    return copied

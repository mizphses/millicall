"""外線発信の回線選択（発信プレフィクス・既定の発信トランク, issue #139）。

ダイヤルプラン（dialplan_default.xml.j2）・MCP 発信・ワークフロー/MCP の transfer が
同じ規則で「どのトランクから発信するか」「外線番号か」を判定するための共通処理。

規則:
  - トランクに発信プレフィクス（dial_prefix）があり、宛先が ``<prefix>`` +
    外線番号（``[184|186]0…``）ならプレフィクスを剥がしてそのトランクから発信する。
  - プレフィクスなしの外線番号は既定の発信トランクから発信する。既定は設定
    ``default_outbound_trunk_id``、未設定・該当トランクが無効/発信不可なら
    発信に使うトランクの名前順先頭（従来挙動）。
  - 発信に使わない（outbound_enabled=False）トランクはどちらにも使わない。
"""

import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

# 発信プレフィクスの書式: 0-9 * # を 1〜8 文字。
DIAL_PREFIX_RE = re.compile(r"^[0-9*#]{1,8}$")

# プレフィクスを剥がした後の外線番号。184/186（発信者番号通知の制御）は事業者へ
# そのまま渡す（例: 92 186 070…）。
EXTERNAL_NUMBER_RE = re.compile(r"^(?:18[46])?0\d+$")

# 184/186 を除いた番号に対する国際番号の判定（dialplan の outbound_intl_block と同じ）。
_CALLER_ID_CONTROL_RE = re.compile(r"^18[46]")


def strip_caller_id_control(number: str) -> str:
    """先頭の 184/186（発信者番号通知の制御）を除いた番号を返す。"""
    return _CALLER_ID_CONTROL_RE.sub("", number, count=1)


def dial_prefix_format_error(prefix: str) -> str | None:
    """発信プレフィクスの書式違反を日本語メッセージで返す（問題なければ None）。

    トランク間の重複・番号プランとの衝突は DB を引く router 側で検証する。
    """
    if not DIAL_PREFIX_RE.fullmatch(prefix):
        return "発信プレフィクスは 0-9・*・# の 1〜8 文字で指定してください"
    if prefix.startswith("0"):
        return "発信プレフィクスは 0 で始められません（外線番号と区別できなくなります）"
    if prefix.startswith(("184", "186")):
        return (
            "184/186 で始まる発信プレフィクスは使えません"
            "（発信者番号通知の制御と区別できなくなります）"
        )
    if prefix.startswith("1") and len(prefix) == 3:
        return (
            "1 で始まる 3 桁の発信プレフィクスは使えません"
            "（184/186 や 110/119 などの特番と衝突します）"
        )
    return None


def prefixes_overlap(a: str, b: str) -> bool:
    """一方が他方の先頭部分になっている（同じダイヤルが両方に一致しうる）か。"""
    return a.startswith(b) or b.startswith(a)


def split_dial_prefix(dest: str, prefixes: Iterable[str]) -> tuple[str, str] | None:
    """dest が ``<prefix>`` + 外線番号なら (prefix, 剥がした番号) を返す。"""
    for prefix in prefixes:
        if prefix and dest.startswith(prefix):
            rest = dest[len(prefix) :]
            if EXTERNAL_NUMBER_RE.fullmatch(rest):
                return prefix, rest
    return None


def is_external_destination(dest: str, prefixes: Iterable[str]) -> bool:
    """外線番号（プレフィクスなし、または任意トランクの発信プレフィクス付き）か。"""
    if EXTERNAL_NUMBER_RE.fullmatch(dest):
        return True
    return split_dial_prefix(dest, prefixes) is not None


def _outbound_enabled(trunk: object) -> bool:
    return bool(getattr(trunk, "outbound_enabled", True))


def outbound_candidates[T](trunks: Iterable[T]) -> list[T]:
    """発信に使うトランクを名前順で返す。"""
    return sorted(
        (t for t in trunks if _outbound_enabled(t)),
        key=lambda t: getattr(t, "name", ""),
    )


def select_default_outbound_trunk[T](
    trunks: Iterable[T], default_trunk_id: int | None
) -> T | None:
    """既定の発信トランクを返す。

    default_trunk_id のトランクが発信候補にあればそれ、無ければ（未設定・無効・
    発信に使わない・削除済み）発信候補の名前順先頭。候補が無ければ None。
    """
    candidates = outbound_candidates(trunks)
    if default_trunk_id is not None:
        for t in candidates:
            if getattr(t, "id", None) == default_trunk_id:
                return t
    return candidates[0] if candidates else None


def prefixed_outbound_trunks[T](trunks: Iterable[T]) -> list[T]:
    """発信プレフィクスを持つ発信候補（書式が正しいものだけ）を名前順で返す。"""
    return [
        t
        for t in outbound_candidates(trunks)
        if (getattr(t, "dial_prefix", "") or "")
        and dial_prefix_format_error(getattr(t, "dial_prefix", "")) is None
    ]


def find_prefixed_trunk[T](dest: str, trunks: Sequence[T]) -> tuple[T, str] | None:
    """dest が発信プレフィクス付きの外線番号なら (トランク, 剥がした番号) を返す。"""
    for t in prefixed_outbound_trunks(trunks):
        hit = split_dial_prefix(dest, [getattr(t, "dial_prefix", "")])
        if hit is not None:
            return t, hit[1]
    return None


# --- transfer 用: 現在の発信プレフィクス一覧を DB から引くローダ ---------------------
# EslCallControl は DB セッションを持たないため、起動時に sessionmaker を使うローダを
# 登録し、transfer のたびに DB から最新のプレフィクス一覧を引く。
_DialPrefixLoader = Callable[[], Awaitable[list[str]]]
_loader: _DialPrefixLoader | None = None


def register_dial_prefix_loader(loader: _DialPrefixLoader | None) -> None:
    """transfer の外線判定に使う発信プレフィクスのローダを登録する（None で解除）。"""
    global _loader
    _loader = loader


async def load_active_dial_prefixes() -> list[str]:
    """発信に使うトランクの発信プレフィクス一覧（ローダ未登録なら空）。"""
    if _loader is None:
        return []
    try:
        return await _loader()
    except Exception:  # noqa: BLE001 — DB 不調でも 0 始まりの外線判定は続ける
        logger.warning("発信プレフィクス一覧を DB から読めないため、プレフィクスなしで判定します")
        return []


def make_db_dial_prefix_loader(sessionmaker) -> _DialPrefixLoader:
    """sessionmaker から enabled かつ発信に使うトランクのプレフィクスを引くローダを作る。"""

    async def _load() -> list[str]:
        from sqlalchemy import select

        from millicall.models import Trunk

        async with sessionmaker() as session:
            rows = await session.execute(
                select(Trunk.dial_prefix).where(
                    Trunk.enabled.is_(True),
                    Trunk.outbound_enabled.is_(True),
                    Trunk.dial_prefix != "",
                )
            )
            return [p for (p,) in rows if DIAL_PREFIX_RE.fullmatch(p)]

    return _load

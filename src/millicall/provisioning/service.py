"""プロビジョニングサービス — デバイス同期・クイックプロビジョニング・再取得指示。

netd 経由で DHCP リースを取得して Device 行を upsert したり、
管理者が指定した内線番号をデバイスに割り当てたりするユースケース関数群。
端末固有 cfg の配信ゲート（provision_mode / 厳格モードのトークン）と取得記録、
SIP 登録状態の取得、電話機への再取得指示（resync）の補助もここに置く。
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.crypto import SecretBox
from millicall.models import Device, Extension
from millicall.net_guard import _check_device_ip, _PinnedTransport
from millicall.network.client import NetdClient
from millicall.network.validation import is_valid_hostname, normalize_mac, validate_ipv4
from millicall.sip_credentials import new_extension_password
from millicall.telephony.esl import ESLError

logger = logging.getLogger("millicall.provisioning")


# IN 句に渡す値の上限。SQLite のバインド変数上限（古い版で 999）に収まる値にする。
_IN_CHUNK_SIZE = 500


def chunked[T](items: Sequence[T], size: int = _IN_CHUNK_SIZE) -> Iterator[Sequence[T]]:
    """items を size 件ずつに分割する（IN 句のバインド変数上限対策）。"""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _utcnow() -> datetime:
    """タイムゾーンなし UTC 現在時刻を返す（DB の他列に合わせて timezone-naive）。"""
    return datetime.now(UTC).replace(tzinfo=None)


async def sync_devices_from_leases(
    session: AsyncSession,
    netd_client: NetdClient,
) -> list[Device]:
    """DHCP リースから Device 行を upsert する。

    netd_client 経由でリースを取得し、既存行は ip/hostname/last_seen を更新、
    未登録 MAC は新規行を追加する（provisioned=False, active=True）。

    不正な MAC / IP はスキップする。不正な hostname は NULL として保存する。

    Args:
        session: SQLAlchemy 非同期セッション。
        netd_client: DHCP リース取得に使う NetdClient インスタンス。

    Returns:
        upsert した Device オブジェクトのリスト。

    Raises:
        NetdError: netd との通信失敗時（呼び出し元が 502 に変換する）。
    """
    leases = await netd_client.get_dhcp_leases()

    now = _utcnow()

    # 検証済みリース (mac, ip, hostname) をリース順に集める（不正な MAC / IP は除外）
    valid: list[tuple[str, str, str | None]] = []
    for lease in leases:
        raw_mac = lease.get("mac", "")
        raw_ip = lease.get("ip", "")
        raw_hostname = lease.get("hostname", "")

        # MAC 正規化 — 不正なら行をスキップ
        try:
            mac = normalize_mac(raw_mac)
        except ValueError:
            logger.debug("sync_devices: 不正な MAC をスキップ: %r", raw_mac)
            continue

        # IP 検証 — 不正なら行をスキップ
        try:
            validate_ipv4(raw_ip)
        except ValueError:
            logger.debug("sync_devices: 不正な IP をスキップ: %r", raw_ip)
            continue

        # hostname は RFC1123 で検証; 不正なら NULL として保存
        hostname: str | None = raw_hostname if is_valid_hostname(raw_hostname) else None
        valid.append((mac, raw_ip, hostname))

    # 既存行を MAC 一覧でまとめて取得する（件数比例の SELECT を避ける）
    by_mac: dict[str, Device] = {}
    unique_macs = list(dict.fromkeys(mac for mac, _, _ in valid))
    for chunk in chunked(unique_macs, _IN_CHUNK_SIZE):
        for existing in await session.scalars(select(Device).where(Device.mac_address.in_(chunk))):
            by_mac[existing.mac_address] = existing

    # リース順に適用する。同一 MAC が複数あれば後勝ち（同じ Device を更新し続ける）で、
    # 返り値にはリースごとに（重複を含めて）その Device が入る。
    devices: list[Device] = []
    for mac, ip, hostname in valid:
        device = by_mac.get(mac)
        if device is not None:
            device.ip_address = ip
            device.hostname = hostname
            device.last_seen = now
            device.active = True
        else:
            device = Device(
                mac_address=mac,
                ip_address=ip,
                hostname=hostname,
                provisioned=False,
                active=True,
                last_seen=now,
                # 未指定の列は flush 後に未ロード扱いになり commit 後の属性アクセスで
                # 遅延ロード（async では MissingGreenlet）になるため、明示しておく。
                model=None,
                extension_id=None,
                provision_token=None,
                last_config_fetch_at=None,
                last_config_fetch_user_agent=None,
            )
            session.add(device)
            by_mac[mac] = device
        devices.append(device)

    await session.commit()

    # 通常は expire_on_commit=False かつ INSERT ... RETURNING で id / created_at まで
    # 読み込み済み。未ロード属性が残る場合（expire_on_commit=True 等）だけ refresh する。
    for d in by_mac.values():
        if sa_inspect(d).unloaded:
            await session.refresh(d)

    return devices


async def quick_provision(
    session: AsyncSession,
    device_id: int,
    extension_number: str,
    display_name: str,
    telephony_notify: Callable[[AsyncSession], Awaitable[None]],
    *,
    secret_box: SecretBox,
) -> Device:
    """デバイスに内線を割り当ててプロビジョニング完了状態にする。

    Extension が存在しない場合は新規作成する（SIP パスワードは自動生成）。
    FreeSWITCH 設定を再生成する。

    トークンについて（issue #85）:
        プロビジョニングトークンはここでは発行しない。端末固有 cfg は既定（standard）で
        LAN + 登録済み端末 + IP 一致 + active/enabled のゲートで配信する。トークン必須の
        厳格モード（strict）は管理者が端末ごとに明示的に選び、トークンも明示的に発行して
        電話機の Web 画面へ手入力する（``issue_provision_token`` / devices_router 参照）。
        以前は電話機管理者資格情報が設定済みだと自動でトークンを発行していたが、
        トークン付き URL を電話機へ配送する手段が無く、電話機が 404 のまま
        登録できなくなっていた。

        内線の割り当てが変わるため、設定ファイル取得記録はリセットする
        （以前の取得は新しい内線の設定を反映していない）。

    Args:
        session: SQLAlchemy 非同期セッション。
        device_id: 対象 Device の主キー。
        extension_number: 割り当てる内線番号（SIP ユーザー名）。
        display_name: 内線の表示名（既存 Extension がある場合は更新しない）。
        telephony_notify: TelephonyChangeListener.notify のような非同期 callable。
                          call signature: ``async (session) -> None``。
        secret_box: 新規作成する内線の SIP パスワードを暗号化する鍵。

    Returns:
        更新済みの Device オブジェクト。

    Raises:
        ValueError: 指定した device_id のデバイスが存在しない場合。
    """
    device = await session.get(Device, device_id)
    if device is None:
        raise ValueError(f"Device not found: id={device_id}")

    # Extension を番号で検索、なければ新規作成
    ext = await session.scalar(select(Extension).where(Extension.number == extension_number))
    if ext is None:
        ext = Extension(
            number=extension_number,
            display_name=display_name,
            sip_password_encrypted=new_extension_password(secret_box)[1],
            enabled=True,
        )
        session.add(ext)
        await session.flush()  # ext.id を確定させる（commit は後）

    device.extension_id = ext.id
    device.provisioned = True
    # 取得確認は新しい割り当てで取り直す（UI の「完了」判定に古い取得を使わない）。
    reset_config_fetch(device)

    await session.commit()
    await session.refresh(device)

    # FreeSWITCH 設定再生成 + reloadxml
    await telephony_notify(session)

    return device


# ---------------------------------------------------------------------------
# 配信ゲートの補助（provision_mode / トークン / 取得記録）— issue #85
# ---------------------------------------------------------------------------

PROVISION_MODE_STANDARD = "standard"
PROVISION_MODE_STRICT = "strict"
PROVISION_MODES = (PROVISION_MODE_STANDARD, PROVISION_MODE_STRICT)

# User-Agent の保存上限（devices.last_config_fetch_user_agent の列長）。
_USER_AGENT_MAX = 255
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def generate_provision_token() -> str:
    """厳格モード用のプロビジョニングトークン（URL セーフ・43 文字）を生成する。"""
    return secrets.token_urlsafe(32)


def hash_provision_token(token: str) -> str:
    """トークンの SHA-256 hex を返す。DB には平文ではなくこの値を保存する。

    トークンは 256bit の乱数のためソルト・ストレッチは不要（総当たり不能）。
    """
    return hashlib.sha256(token.encode()).hexdigest()


def provision_token_matches(device: Device, token: str) -> bool:
    """提示されたトークンが Device に保存済みのトークン（ハッシュ）と一致するか。"""
    if device.provision_token is None:
        return False
    return hmac.compare_digest(
        hash_provision_token(token).encode(), device.provision_token.encode()
    )


def sanitize_user_agent(user_agent: str | None) -> str | None:
    """User-Agent から制御文字を除き、列長に収める（管理 UI 表示用）。"""
    if not user_agent:
        return None
    cleaned = _CONTROL_CHARS_RE.sub("", user_agent).strip()
    return cleaned[:_USER_AGENT_MAX] or None


def record_config_fetch(device: Device, *, user_agent: str | None, used_token: bool) -> None:
    """端末固有 cfg を配信したことを Device に記録する（commit は呼び出し元）。"""
    device.last_config_fetch_at = _utcnow()
    device.last_config_fetch_user_agent = sanitize_user_agent(user_agent)
    device.last_config_fetch_used_token = used_token


def reset_config_fetch(device: Device) -> None:
    """取得記録を未取得に戻す（内線の再割り当て・トークン再発行時など）。"""
    device.last_config_fetch_at = None
    device.last_config_fetch_user_agent = None
    device.last_config_fetch_used_token = False


def detect_vendor(device: Device) -> str | None:
    """取得時の User-Agent（なければ model）から "yealink" / "panasonic" を推定する。

    推定できなければ None（resync では全ベンダーの手段を試す）。
    """
    for source in (device.last_config_fetch_user_agent, device.model):
        if not source:
            continue
        low = source.lower()
        if "yealink" in low:
            return "yealink"
        if "panasonic" in low or "kx-" in low:
            return "panasonic"
    return None


# ---------------------------------------------------------------------------
# FreeSWITCH の SIP 登録状態 / check-sync NOTIFY（ESL 経由）
# ---------------------------------------------------------------------------

# ESL 疎通の最大待ち時間（秒）。trunks/router の _STATUS_TIMEOUT と同水準。
_ESL_TIMEOUT = 5.0

# check_sync に渡す user@domain の user 部の許容形式。ESL コマンドへの改行・空白・
# 制御文字の混入（コマンド注入）を防ぐため、英数と限られた記号のみに厳格に絞る。
# domain 部は validate_ipv4 / is_valid_hostname で検証する。
_SIP_USER_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")

EslFactory = Callable[[], Any]


@dataclass(frozen=True)
class SipRegistration:
    """``sofia status profile internal reg`` の 1 登録分。"""

    user: str  # "1001@192.168.10.1"（User: 行そのまま）
    ip: str | None  # 登録元 IP（IP: 行。古い FreeSWITCH では無いことがある）

    @property
    def user_part(self) -> str:
        return self.user.split("@", 1)[0]


def parse_sofia_registrations(output: str) -> list[SipRegistration]:
    """``sofia status profile internal reg`` の出力から登録一覧を取り出す。

    出力は ``Call-ID:`` で始まる「キー: 値」行のブロックの並び。User / IP のみ使う。
    """
    regs: list[SipRegistration] = []
    current: dict[str, str] | None = None

    def _flush() -> None:
        if current and current.get("User"):
            regs.append(SipRegistration(user=current["User"], ip=current.get("IP") or None))

    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key == "Call-ID":
            _flush()
            current = {}
        if current is not None:
            current[key] = value.strip()
    _flush()
    return regs


async def fetch_sip_registrations(
    esl_factory: EslFactory,
    *,
    timeout: float = _ESL_TIMEOUT,
) -> list[SipRegistration] | None:
    """FreeSWITCH internal プロファイルの登録一覧を取得する。

    FreeSWITCH へ到達できない場合は None（＝未確認。「登録なし」と区別する）。
    接続は trunks/router の trunk_status と同様に要求ごとに張って閉じる。
    """
    client = esl_factory()

    async def _query() -> str:
        await client.connect()
        return await client.api("sofia status profile internal reg")

    try:
        out = await asyncio.wait_for(_query(), timeout=timeout)
    except (TimeoutError, OSError, ESLError):
        return None
    finally:
        await client.close()
    return parse_sofia_registrations(out)


def device_is_registered(
    device: Device, extension_number: str | None, regs: Sequence[SipRegistration]
) -> bool:
    """Device の内線が、Device の IP から登録されているか。

    同じ内線を別端末（ソフトフォン等）で登録している場合に誤って「登録済み」と
    しないよう、登録元 IP が取れる場合は IP 一致も要求する。
    """
    if not extension_number or not device.ip_address:
        return False
    for reg in regs:
        if reg.user_part != extension_number:
            continue
        if reg.ip is None or reg.ip == device.ip_address:
            return True
    return False


def valid_check_sync_target(user_at_domain: str) -> bool:
    """check_sync の引数（user@domain）が厳格な形式に合うか。"""
    user, sep, domain = user_at_domain.partition("@")
    if not sep or not _SIP_USER_RE.match(user):
        return False
    try:
        validate_ipv4(domain)
        return True
    except ValueError:
        return is_valid_hostname(domain)


@dataclass
class ResyncAttempt:
    """再取得指示の 1 手段の結果。

    ok は「指示が受理された」ことだけを表す（電話機が実際に設定を取りに来たかは
    取得記録 ``last_config_fetch_at`` で確認する）。verified=False の手段は
    ベンダー資料で確認できていない（実機未検証）。skipped=True は送信していない。
    """

    method: str
    ok: bool
    detail: str
    verified: bool = True
    skipped: bool = False


@dataclass
class ResyncResult:
    """resync_phone の構造化結果。"""

    attempts: list[ResyncAttempt] = field(default_factory=list)

    @property
    def requested(self) -> bool:
        """1 つ以上の手段で再取得の指示が受理されたか。"""
        return any(a.ok for a in self.attempts)


_CHECK_SYNC = "sip_notify_check_sync"


async def _sip_check_sync(
    device: Device,
    esl_factory: EslFactory,
    *,
    timeout: float,
) -> ResyncAttempt:
    """端末 IP から登録中の SIP ユーザーへ check-sync NOTIFY を送らせる。

    FreeSWITCH の ``sofia profile internal check_sync <user>@<domain>`` を使う。
    宛先は「Device の IP から登録している登録」の User をそのまま使う（再割り当て直後で
    旧内線のまま登録している端末にも届くよう、DB の内線番号ではなく登録から引く）。
    """
    regs = await fetch_sip_registrations(esl_factory, timeout=timeout)
    if regs is None:
        return ResyncAttempt(_CHECK_SYNC, False, "FreeSWITCH に接続できません", skipped=True)
    target = next((r.user for r in regs if r.ip and r.ip == device.ip_address), None)
    if target is None:
        return ResyncAttempt(
            _CHECK_SYNC, False, "この端末の SIP 登録が見つかりません", skipped=True
        )
    if not valid_check_sync_target(target):
        logger.warning("resync_phone: check_sync 宛先の形式が不正なため送信しない: %r", target)
        return ResyncAttempt(_CHECK_SYNC, False, "SIP 登録ユーザーの形式が不正です", skipped=True)

    client = esl_factory()

    async def _send() -> str:
        await client.connect()
        return await client.api(f"sofia profile internal check_sync {target}")

    try:
        out = await asyncio.wait_for(_send(), timeout=timeout)
    except (TimeoutError, OSError, ESLError):
        return ResyncAttempt(_CHECK_SYNC, False, "FreeSWITCH への送信に失敗しました")
    finally:
        await client.close()
    if out.startswith("+OK"):
        return ResyncAttempt(_CHECK_SYNC, True, f"NOTIFY を {target} へ送信しました")
    return ResyncAttempt(_CHECK_SYNC, False, f"FreeSWITCH が拒否しました: {out[:80]}")


async def _http_get_attempt(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None,
    timeout: float,
    verified: bool,
) -> ResyncAttempt:
    """HTTP GET を 1 回送り、2xx のときだけ成功とする（3xx のログイン画面等は失敗）。"""
    try:
        r = await client.get(url, headers=headers, timeout=timeout)
    except Exception:  # noqa: BLE001 — 電話機側の失敗はすべて「指示できなかった」扱い
        return ResyncAttempt(method, False, "接続できません", verified=verified)
    ok = 200 <= r.status_code < 300
    return ResyncAttempt(method, ok, f"HTTP {r.status_code}", verified=verified)


async def resync_phone(
    device: Device,
    *,
    admin_username: str,
    admin_password: str,
    esl_factory: EslFactory | None = None,
    http_timeout: float = 5.0,
    esl_timeout: float = _ESL_TIMEOUT,
) -> ResyncResult:
    """電話機へ設定の再取得を指示する（ベストエフォート。例外は伝播させない）。

    これは「再取得の指示」であって設定の配送ではない。電話機は自分に設定済みの
    プロビジョニング URL（通常は DHCP option 66、厳格モードでは管理者が手入力した
    トークン付き URL）から取りに来る。実際に取りに来たかは取得記録で確認する。

    手段（結果はすべて ``ResyncResult.attempts`` に残す）:
      1. SIP NOTIFY check-sync（``esl_factory`` 指定時。端末 IP から SIP 登録中のときのみ）。
         Panasonic は ConfigCommon.cfg の CFG_RESYNC_FROM_SIP="check-sync"、Yealink は
         check-sync に反応する想定（いずれも実機要確認。Yealink は設定により再起動を伴う）。
      2. HTTP。ベンダーは取得時の User-Agent から推定し、不明なら全手段を試す。
         成功判定は 2xx のみ。最初に成功した時点で残りの HTTP 手段は送らない。
         - Yealink Action URI ``/servlet?key=AutoP``（資料あり。common.cfg で配る
           features.action_uri_limit_ip に PBX の IP が入っている必要がある）
         - Panasonic ``/admin/resync``（**未検証**: ベンダー資料で確認できていない）
         - Panasonic ``/cgi-bin/api-provision?event=resync``（**未検証**・認証なし）

    電話機管理者資格情報（``admin_username`` / ``admin_password``）は Basic 認証に使う。
    どちらかが空なら HTTP の手段は一切送らない（空の資格情報を送らない fail-closed
    動作。監査 M2）。SIP NOTIFY は資格情報に依存しないため送る。

    セキュリティ: SSRF ガード (M4)
      * ``_check_device_ip`` でデバイス IP をループバック/リンクローカル/マルチキャスト/
        予約済み/未指定アドレスから拒否する（169.254.169.254 メタデータアドレスを含む）。
        電話機は RFC1918 LAN 上にあるためプライベート IP は許可する。拒否時は NOTIFY も送らない。
      * ``_PinnedTransport`` で IP リテラルに接続を固定し、リダイレクトは辿らない。
        これにより認証情報付きリクエストがリダイレクト先へ漏洩するのを防ぐ。
      * 残存リスク: 同一 LAN 上で ARP スプーフィング等が行われた場合、同セグメントの
        任意ホストへ認証情報が届く可能性はある。LAN 物理セキュリティが前提。
      * ESL コマンドの引数（check_sync 宛先）は ``valid_check_sync_target`` で厳格に検証する。

    Args:
        device: IP アドレスを持つ Device オブジェクト。
        admin_username: 電話機 Web 管理者ユーザ名（Settings 由来。コードに定数を持たない）。
        admin_password: 電話機 Web 管理者パスワード（Settings 由来）。
        esl_factory: ESLClient を生成する callable。None なら SIP NOTIFY を使わない。
        http_timeout: HTTP 1 リクエストのタイムアウト（秒）。
        esl_timeout: ESL 1 操作のタイムアウト（秒）。

    Returns:
        各手段の結果を持つ ResyncResult。
    """
    result = ResyncResult()
    if not device.ip_address:
        result.attempts.append(ResyncAttempt("http", False, "IP アドレスが不明です", skipped=True))
        return result

    ip = device.ip_address

    # --- SSRF ガード: デバイス IP の分類チェック ---
    try:
        _check_device_ip(ip)
    except ValueError:
        logger.warning("resync_phone: デバイス IP %r はブロック対象アドレスのため中断", ip)
        result.attempts.append(
            ResyncAttempt("http", False, "ブロック対象の IP アドレスです", skipped=True)
        )
        return result

    if esl_factory is not None:
        try:
            result.attempts.append(await _sip_check_sync(device, esl_factory, timeout=esl_timeout))
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("resync_phone: check_sync で予期しない例外 ip=%s", ip, exc_info=True)
            result.attempts.append(ResyncAttempt(_CHECK_SYNC, False, "予期しないエラー"))

    # 資格情報が未設定(空)なら HTTP での指示は送らない（監査 M2: phone_admin の既定は空。
    # 空の Basic 認証を電話機へ送らない fail-closed 動作）。運用者が
    # MILLICALL_PHONE_ADMIN_USERNAME/PASSWORD（または管理画面）で設定して初めて有効になる。
    if not admin_username or not admin_password:
        logger.info("resync_phone: 電話機管理者資格情報が未設定のため HTTP での指示をスキップ")
        result.attempts.append(
            ResyncAttempt(
                "http",
                False,
                "電話機管理者資格情報が未設定のため HTTP での指示は送りません",
                skipped=True,
            )
        )
        return _log_result(result, ip)

    creds = base64.b64encode(f"{admin_username}:{admin_password}".encode()).decode()
    auth_headers = {"Authorization": f"Basic {creds}"}

    vendor = detect_vendor(device)
    # (method, url, 送る資格情報, 資料で確認済みか)
    candidates: list[tuple[str, str, dict[str, str] | None, bool]] = []
    if vendor in (None, "yealink"):
        candidates.append(
            ("yealink_action_uri", f"http://{ip}/servlet?key=AutoP", auth_headers, True)
        )
    if vendor in (None, "panasonic"):
        candidates.append(
            ("panasonic_admin_resync", f"http://{ip}/admin/resync", auth_headers, False)
        )
        # 認証不要の手段には資格情報を付けない（不要な送信先へ渡さない）。
        candidates.append(
            (
                "panasonic_cgi_provision",
                f"http://{ip}/cgi-bin/api-provision?event=resync",
                None,
                False,
            )
        )

    # IP リテラルを直接使うため DNS 再解決なし; _PinnedTransport で再接続も同 IP に固定。
    transport = _PinnedTransport(ip, ip)

    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        for method, url, headers, verified in candidates:
            attempt = await _http_get_attempt(
                client, method, url, headers=headers, timeout=http_timeout, verified=verified
            )
            result.attempts.append(attempt)
            if attempt.ok:
                break

    return _log_result(result, ip)


def _log_result(result: ResyncResult, ip: str) -> ResyncResult:
    if result.requested:
        logger.info(
            "resync_phone: 再取得を指示 ip=%s methods=%s",
            ip,
            ",".join(a.method for a in result.attempts if a.ok),
        )
    else:
        logger.warning("resync_phone: どの手段でも再取得を指示できず ip=%s", ip)
    return result

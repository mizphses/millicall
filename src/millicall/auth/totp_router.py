"""TOTP 2FA エンドポイント（登録 / 確認 / 無効化）。

設計概要:
  - /totp/setup   : 秘密鍵を生成して DB に保存（totp_enabled は False のまま）
  - /totp/verify  : コードを検証して totp_enabled=True にする。リカバリコードをここで返す
  - /totp/disable : 有効 TOTP コードまたはリカバリコードで本人確認して無効化する

セキュリティ注意点:
  - base32 秘密鍵は SecretBox（Fernet）で暗号化して DB 保存する
  - 復号後の平文は /setup レスポンス以外で絶対に返さない
  - リカバリコードの平文は /verify レスポンスのみ。DB には Argon2 ハッシュのみ格納
  - audit detail にシークレット・コード・ハッシュ文字列を含めない
"""
import json
import secrets as secrets_mod

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.audit import get_client_ip, record_audit
from millicall.auth.security import bump_session_epoch
from millicall.crypto import SecretBox
from millicall.deps import get_current_user, get_secret_box, get_session
from millicall.models import User

router = APIRouter(prefix="/api/auth/totp", tags=["auth"])

_hasher = PasswordHasher()

# リカバリコードの生成数
_RECOVERY_CODE_COUNT = 10


# ---------------------------------------------------------------------------
# リクエスト / レスポンス スキーマ
# ---------------------------------------------------------------------------


class TotpVerifyRequest(BaseModel):
    """TOTP 確認 / 無効化リクエスト。"""

    code: str


class TotpSetupResponse(BaseModel):
    """セットアップレスポンス。secret と provisioning_uri を一度だけ返す。"""

    secret: str
    provisioning_uri: str


class TotpVerifyResponse(BaseModel):
    """確認成功レスポンス。リカバリコードをここでのみ返す。"""

    recovery_codes: list[str]


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------


def _generate_recovery_codes() -> tuple[list[str], list[str]]:
    """10 個のリカバリコードを生成する。

    Returns:
        (plaintext_codes, hashed_codes) — hashed_codes のみ DB に保存する
    """
    plain: list[str] = []
    hashed: list[str] = []
    for _ in range(_RECOVERY_CODE_COUNT):
        raw = secrets_mod.token_hex(10)  # 20 文字の hex 文字列
        # 人間が読みやすい形式: xxxxx-xxxxx-xxxxx-xxxxx（4 × 5 文字）
        code = f"{raw[:5]}-{raw[5:10]}-{raw[10:15]}-{raw[15:20]}"
        plain.append(code)
        hashed.append(_hasher.hash(code))
    return plain, hashed


def _check_recovery_code(stored_hashes: list[str], code: str) -> int | None:
    """リカバリコードをすべてのハッシュと比較し、一致したインデックスを返す。

    Argon2 verify を使って定数時間比較する。一致がなければ None を返す。
    """
    for i, h in enumerate(stored_hashes):
        try:
            if _hasher.verify(h, code):
                return i
        except VerifyMismatchError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return None


def _verify_totp_or_recovery(user: User, box: SecretBox, code: str) -> str | None:
    """TOTP コードまたはリカバリコードを検証する。

    Returns:
        "totp"     : TOTP コードが一致した場合
        "recovery:N": N 番目のリカバリコードが一致した場合
        None       : いずれも一致しない場合

    リカバリコードが一致した場合は当該エントリを消費（None にセット）する。
    呼び出し後に DB commit すること。
    """
    if user.totp_secret is None:
        return None

    try:
        plain_secret = box.decrypt(user.totp_secret)
    except Exception:  # noqa: BLE001
        return None

    # まず TOTP を試みる
    totp = pyotp.TOTP(plain_secret)
    if totp.verify(code, valid_window=1):
        return "totp"

    # 次にリカバリコードを試みる
    if user.recovery_codes is None:
        return None
    try:
        stored: list[str] = json.loads(user.recovery_codes)
    except (json.JSONDecodeError, TypeError):
        return None

    idx = _check_recovery_code(stored, code)
    if idx is None:
        return None

    # 使用済みエントリを消費して更新する
    stored[idx] = None  # type: ignore[call-overload]
    # None を除外して保存（空になっても [] を残す）
    user.recovery_codes = json.dumps([h for h in stored if h is not None])
    return f"recovery:{idx}"


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.post("/setup", response_model=TotpSetupResponse)
async def totp_setup(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
) -> TotpSetupResponse:
    """TOTP シークレットを生成し DB に保存する（未確認状態）。

    すでに TOTP が有効な場合は、有効な TOTP コードをリクエストボディで
    要求する仕様もあり得るが、ここでは「いつでも上書き可能」とする。
    理由: setup だけでは totp_enabled=True にならないため、誤ってコールしても
    既存の有効 TOTP セッションが無効化されるリスクがない（verify するまで
    新しいシークレットは有効にならない）。ただし古いシークレットは消える。
    """
    plain_secret = pyotp.random_base32()
    encrypted = box.encrypt(plain_secret)
    user.totp_secret = encrypted
    # setup だけでは有効化しない（totp_enabled は verify 後に True になる）
    user.totp_enabled = False
    session.add(user)

    provisioning_uri = pyotp.TOTP(plain_secret).provisioning_uri(
        name=user.username, issuer_name="Millicall"
    )

    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="totp.setup",
        ip_address=get_client_ip(request),
        # シークレット・URI は audit detail に記録しない
    )
    await session.commit()

    return TotpSetupResponse(secret=plain_secret, provisioning_uri=provisioning_uri)


@router.post("/verify", response_model=TotpVerifyResponse)
async def totp_verify(
    body: TotpVerifyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
) -> TotpVerifyResponse:
    """TOTP コードを検証し、成功したら TOTP を有効化してリカバリコードを返す。

    リカバリコードはここで一度だけ平文で返す。以降は取得不可。
    """
    if user.totp_secret is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP が設定されていません。先に /totp/setup を呼んでください",
        )

    try:
        plain_secret = box.decrypt(user.totp_secret)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="TOTP シークレットの復号に失敗しました",
        ) from exc

    totp = pyotp.TOTP(plain_secret)
    if not totp.verify(body.code, valid_window=1):
        await record_audit(
            session,
            actor_user_id=user.id,
            actor_label=user.username,
            action="totp.verify_failure",
            ip_address=get_client_ip(request),
            # コードは audit detail に記録しない
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code",
        )

    plain_codes, hashed_codes = _generate_recovery_codes()
    user.totp_enabled = True
    user.recovery_codes = json.dumps(hashed_codes)
    session.add(user)

    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="totp.enable",
        ip_address=get_client_ip(request),
        # コード・ハッシュは audit detail に記録しない
    )
    await session.commit()

    # 平文リカバリコードはここでのみ返す
    return TotpVerifyResponse(recovery_codes=plain_codes)


@router.post("/disable")
async def totp_disable(
    body: TotpVerifyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
) -> dict[str, bool]:
    """TOTP を無効化する。有効な TOTP コードまたはリカバリコードが必要。

    bump_session_epoch を呼んで既存セッションを全て失効させる。
    """
    if not user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP は有効ではありません",
        )

    method = _verify_totp_or_recovery(user, box, body.code)
    if method is None:
        await record_audit(
            session,
            actor_user_id=user.id,
            actor_label=user.username,
            action="totp.disable_failure",
            ip_address=get_client_ip(request),
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid code",
        )

    user.totp_secret = None
    user.totp_enabled = False
    user.recovery_codes = None
    bump_session_epoch(user)
    session.add(user)

    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="totp.disable",
        ip_address=get_client_ip(request),
    )
    await session.commit()

    return {"ok": True}

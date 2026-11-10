"""SIP 認証情報（内線 sip_password / トランク password）の暗号化保存ヘルパ。

DB には SecretBox（master_key 由来の Fernet）で暗号化したトークンだけを保存する
（Extension.sip_password_encrypted / Trunk.password_encrypted）。平文が必要になるのは
次の場面だけで、いずれも直前にここで復号する:

  - FreeSWITCH 設定生成（directory/*.xml, sip_profiles/external_*.xml）
  - 電話機 cfg の描画（provisioning）
  - credentials API（管理者限定・監査記録あり）

復号に失敗した場合（鍵不一致・改ざん）は黙って空文字へ劣化させない。個別の復号は
SipCredentialError を送出し（API は 503）、設定生成では該当の内線・トランクを除外して
書く（load_enabled_sip_credentials）。例外メッセージ・ログには平文も暗号文も含めない。
"""

import logging
from dataclasses import dataclass, field

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.crypto import SecretBox
from millicall.gen import generate_sip_password
from millicall.models import Extension, Trunk

logger = logging.getLogger("millicall.sip_credentials")


class SipCredentialError(RuntimeError):
    """暗号化保存された SIP 認証情報を復号できない（鍵不一致・データ破損）。"""


def encrypt_sip_password(box: SecretBox, plaintext: str) -> str:
    """SIP パスワードを保存用に暗号化する。空文字は空文字のまま（トランクの未設定状態）。"""
    if plaintext == "":
        return ""
    return box.encrypt(plaintext)


def new_extension_password(box: SecretBox) -> tuple[str, str]:
    """内線用の SIP パスワードを生成し (平文, 暗号文) を返す。"""
    plaintext = generate_sip_password()
    return plaintext, box.encrypt(plaintext)


def _decrypt(box: SecretBox, token: str, *, what: str) -> str:
    try:
        return box.decrypt(token)
    except (InvalidToken, ValueError, UnicodeError):
        raise SipCredentialError(
            f"{what} の SIP 認証情報を復号できません（master_key 不一致またはデータ破損）。"
            "data/secrets.json を正しいバックアップから戻すか、"
            "python -m millicall.tools.recover_sip で再設定してください"
        ) from None


def decrypt_extension_password(box: SecretBox, ext: Extension) -> str:
    """内線の SIP パスワードを復号する。内線は空パスワードを許さない。"""
    what = f"内線 {ext.number}"
    if not ext.sip_password_encrypted:
        raise SipCredentialError(f"{what} の SIP パスワードが未設定です")
    return _decrypt(box, ext.sip_password_encrypted, what=what)


def decrypt_trunk_password(box: SecretBox, trunk: Trunk) -> str:
    """トランクのパスワードを復号する。空文字は「再入力待ち」の未設定状態として空を返す。"""
    if not trunk.password_encrypted:
        return ""
    return _decrypt(box, trunk.password_encrypted, what=f"トランク {trunk.name}")


@dataclass
class SipCredentialStatus:
    """直近の設定生成（起動時を含む）で復号できなかった項目。番号/名前だけを持つ。"""

    undecryptable_extensions: list[str] = field(default_factory=list)
    undecryptable_trunks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.undecryptable_extensions and not self.undecryptable_trunks


@dataclass
class DecryptedSipCredentials:
    """設定生成に使う有効な内線・トランクと、その平文パスワード（呼び出し側で即座に使い捨てる）。"""

    extensions: list[tuple[Extension, str]] = field(default_factory=list)
    trunks: list[tuple[Trunk, str]] = field(default_factory=list)
    status: SipCredentialStatus = field(default_factory=SipCredentialStatus)


async def load_enabled_sip_credentials(
    session: AsyncSession, box: SecretBox
) -> DecryptedSipCredentials:
    """有効な内線・トランクを読み、パスワードを復号する。

    復号できない行は結果から除外し、番号/名前を status に記録する（FreeSWITCH 設定から
    外れるのでその端末/トランクは登録できない = fail-closed）。空パスワードで書き出す
    ことはしない。起動時チェックと設定生成は同じ対象（有効な行）をこの関数で判定する。
    """
    result = DecryptedSipCredentials()
    for ext in await session.scalars(
        select(Extension).where(Extension.enabled.is_(True)).order_by(Extension.number)
    ):
        try:
            result.extensions.append((ext, decrypt_extension_password(box, ext)))
        except SipCredentialError:
            result.status.undecryptable_extensions.append(ext.number)
    for trunk in await session.scalars(
        select(Trunk).where(Trunk.enabled.is_(True)).order_by(Trunk.name)
    ):
        try:
            result.trunks.append((trunk, decrypt_trunk_password(box, trunk)))
        except SipCredentialError:
            result.status.undecryptable_trunks.append(trunk.name)
    return result


def log_sip_credential_status(status: SipCredentialStatus) -> None:
    if status.ok:
        return
    logger.error(
        "SIP 認証情報を復号できません（master_key 不一致の可能性）: 内線=%s トランク=%s — "
        "これらは FreeSWITCH 設定から除外しました（登録できません）。"
        "data/secrets.json を正しいバックアップから戻して再起動するか、"
        "鍵を失った場合は python -m millicall.tools.recover_sip を実行してください",
        ",".join(status.undecryptable_extensions) or "-",
        ",".join(status.undecryptable_trunks) or "-",
    )

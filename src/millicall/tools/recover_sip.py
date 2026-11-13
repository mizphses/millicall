"""master_key 喪失時の SIP 認証情報の復旧。

    python -m millicall.tools.recover_sip [--yes]

まず data/secrets.json を正しいバックアップから戻せないか確認すること。戻せれば
このコマンドは不要（core 再起動だけで復旧する）。

現在の master_key で復号できない行だけを対象に:
  - 内線: SIP パスワードを再生成する（端末の再設定が必要）
  - トランク: パスワードを空（再入力が必要）にして無効化する

既定は対象を表示するだけ（dry-run）。``--yes`` で実行する。secrets.json が無い場合、
``--yes`` のときだけ新しい鍵を作成する（他の暗号化設定 — AI プロバイダ API キー、
TOTP、SMTP/電話機管理者パスワード、Tailscale 認証キー — も読めなくなるので再設定が必要）。
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.config import Settings, get_settings
from millicall.crypto import SecretBox
from millicall.models import Extension, Trunk
from millicall.secrets_store import load_or_create_secrets
from millicall.sip_credentials import (
    SipCredentialError,
    decrypt_extension_password,
    decrypt_trunk_password,
    new_extension_password,
)
from millicall.tools._common import RESTART_HINT, open_session, out


@dataclass
class RecoveryResult:
    regenerated_extensions: list[str] = field(default_factory=list)
    disabled_trunks: list[str] = field(default_factory=list)


def _decryptable(decrypt, box: SecretBox, row) -> bool:
    try:
        decrypt(box, row)
    except SipCredentialError:
        return False
    return True


async def recover_sip_credentials(
    session: AsyncSession, box: SecretBox | None, *, apply: bool
) -> RecoveryResult:
    """復号できない内線/トランクを洗い出し、apply=True なら再設定してコミットする。

    box=None は「鍵が無い」状態で、全行を対象として扱う（dry-run 専用）。
    """
    if box is None and apply:
        raise ValueError("apply には鍵が必要です")
    result = RecoveryResult()
    for ext in await session.scalars(select(Extension).order_by(Extension.number)):
        if box is not None and _decryptable(decrypt_extension_password, box, ext):
            continue
        result.regenerated_extensions.append(ext.number)
        if apply:
            _, ext.sip_password_encrypted = new_extension_password(box)
    for trunk in await session.scalars(select(Trunk).order_by(Trunk.name)):
        if not trunk.password_encrypted:
            continue  # 既に未設定（再入力待ち）
        if box is not None and _decryptable(decrypt_trunk_password, box, trunk):
            continue
        result.disabled_trunks.append(trunk.name)
        if apply:
            trunk.password_encrypted = ""
            trunk.enabled = False
    if apply:
        await session.commit()
    return result


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    secrets_path = settings.data_dir / "secrets.json"
    box: SecretBox | None = None
    if secrets_path.exists() or args.yes:
        if not secrets_path.exists():
            out(f"secrets ファイルが無いため新しい鍵を作成します: {secrets_path}")
        box = SecretBox(load_or_create_secrets(settings.data_dir).master_key)
    else:
        out(f"secrets ファイルがありません: {secrets_path}（--yes で新しい鍵を作成します）")

    async with open_session(settings) as session:
        result = await recover_sip_credentials(session, box, apply=args.yes)

    if not result.regenerated_extensions and not result.disabled_trunks:
        out("復号できない SIP 認証情報はありません（復旧は不要です）")
        return 0

    verb_ext = "再生成しました" if args.yes else "再生成の対象"
    verb_trunk = "無効化しました（パスワード再入力が必要）" if args.yes else "無効化の対象"
    out(f"内線 SIP パスワードを{verb_ext}: {', '.join(result.regenerated_extensions) or '-'}")
    out(f"トランクを{verb_trunk}: {', '.join(result.disabled_trunks) or '-'}")
    if not args.yes:
        out("実行するには --yes を付けてください（dry-run）")
        return 0
    out(RESTART_HINT)
    out(
        "内線: 各端末の再設定が必要です（プロビジョニング端末は再起動/resync、"
        "ソフトフォンは管理画面の接続情報で新しいパスワードを確認）。"
    )
    out("トランク: 管理画面でパスワードを再入力してから有効化してください。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m millicall.tools.recover_sip",
        description="master_key 喪失時に SIP 認証情報を再設定する（既定は dry-run）",
    )
    parser.add_argument("--yes", action="store_true", help="実際に再設定する")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args, get_settings()))


if __name__ == "__main__":
    sys.exit(main())

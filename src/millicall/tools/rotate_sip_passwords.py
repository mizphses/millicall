"""内線 SIP パスワードの一括再生成。

    python -m millicall.tools.rotate_sip_passwords [--number 1001 ...] [--yes]

既定は対象を表示するだけ（dry-run）。``--yes`` で実際に再生成して DB に暗号化保存する。
新しいパスワードは表示しない（ログ・端末履歴に残さないため）。必要なら管理画面の
内線「接続情報」（credentials API・監査記録あり）で確認する。

再生成後は次が必要:
  - core 再起動（FreeSWITCH 設定の再生成）
  - 端末側の再取得（プロビジョニング端末は再起動/resync、ソフトフォンは手動で再設定）
"""

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.config import Settings, get_settings
from millicall.crypto import SecretBox
from millicall.models import Extension
from millicall.secrets_store import Secrets
from millicall.sip_credentials import new_extension_password
from millicall.tools._common import RESTART_HINT, open_session, out


async def rotate_extension_passwords(
    session: AsyncSession,
    box: SecretBox,
    numbers: list[str] | None = None,
    *,
    apply: bool,
) -> list[str]:
    """対象内線の番号一覧を返す。apply=True のときだけ再生成してコミットする。"""
    stmt = select(Extension).order_by(Extension.number)
    if numbers:
        stmt = stmt.where(Extension.number.in_(numbers))
    targets = list(await session.scalars(stmt))
    if apply:
        for ext in targets:
            _, ext.sip_password_encrypted = new_extension_password(box)
        await session.commit()
    return [e.number for e in targets]


def _load_existing_secrets(settings: Settings) -> Secrets:
    # 鍵を新規作成してはならない（既存の暗号文が読めなくなる）ため load_or_create は使わない。
    path = settings.data_dir / "secrets.json"
    if not path.exists():
        raise SystemExit(f"secrets ファイルがありません: {path}")
    return Secrets.model_validate_json(path.read_text(encoding="utf-8"))


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    secrets = _load_existing_secrets(settings)
    box = SecretBox(secrets.master_key)
    async with open_session(settings) as session:
        numbers = await rotate_extension_passwords(
            session, box, args.number or None, apply=args.yes
        )
    if args.number:
        missing = sorted(set(args.number) - set(numbers))
        if missing:
            out(f"存在しない内線: {', '.join(missing)}")
    if not numbers:
        out("対象の内線はありません")
        return 0
    if not args.yes:
        out(f"再生成の対象（{len(numbers)} 件）: {', '.join(numbers)}")
        out("実行するには --yes を付けてください（dry-run）")
        return 0
    out(f"SIP パスワードを再生成しました（{len(numbers)} 件）: {', '.join(numbers)}")
    out(RESTART_HINT)
    out(
        "各端末の再設定が必要です: プロビジョニング端末は再起動または resync で cfg を"
        "再取得させ、ソフトフォンは管理画面の接続情報で新しいパスワードを確認して設定してください。"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m millicall.tools.rotate_sip_passwords",
        description="内線 SIP パスワードを再生成する（既定は dry-run）",
    )
    parser.add_argument(
        "--number", action="append", help="対象の内線番号（複数指定可。省略時は全内線）"
    )
    parser.add_argument("--yes", action="store_true", help="実際に再生成する")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args, get_settings()))


if __name__ == "__main__":
    sys.exit(main())

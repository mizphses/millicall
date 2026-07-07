"""netd エントリポイント。

``python -m millicall.netd`` で起動する。

設定は環境変数 ``MILLICALL_*`` から読み込む (pydantic-settings)。
"""

import asyncio
import logging
import sys

from millicall.config import get_settings
from millicall.netd.server import serve
from millicall.netd.system import RealSystemOps


def main() -> None:
    """netd デーモンを起動する。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("millicall.netd")
    logger.info("netd 起動中...")

    settings = get_settings()
    ops = RealSystemOps()

    try:
        asyncio.run(serve(settings, ops))
    except KeyboardInterrupt:
        logger.info("netd 停止 (KeyboardInterrupt)")
    except Exception:
        logger.exception("netd が予期しないエラーで終了しました")
        sys.exit(1)


if __name__ == "__main__":
    main()

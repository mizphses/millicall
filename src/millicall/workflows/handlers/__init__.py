"""ワークフローノードハンドラパッケージ (Phase 4b).

このパッケージをインポートすると、各モジュールがインポート副作用として
:func:`~millicall.workflows.executor.register_handler` を呼び出し、
グローバルハンドラレジストリにハンドラを登録する。

Task 4 ロジック系: condition / set_variable / time_condition / api_call
"""

from millicall.workflows.handlers import logic  # noqa: F401

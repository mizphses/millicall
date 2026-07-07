"""ワークフローノードハンドラパッケージ (Phase 4b).

このパッケージをインポートすると、各モジュールがインポート副作用として
:func:`~millicall.workflows.executor.register_handler` を呼び出し、
グローバルハンドラレジストリにハンドラを登録する。

Task 4 ロジック系: condition / set_variable / time_condition / api_call
Task 5 音声系: play_audio / transfer / voicemail / human_escalation
Task 6 DTMF系: dtmf_input / menu
Task 7 AI 会話系: ai_conversation / intent_detection / collect_info
"""

from millicall.workflows.handlers import (
    ai,  # noqa: F401
    audio,  # noqa: F401
    dtmf,  # noqa: F401
    email,  # noqa: F401
    logic,  # noqa: F401
)

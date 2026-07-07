"""special カテゴリのノードハンドラ（Phase 4b Task 11 追補: call_workflow）。

``call_workflow`` は別のワークフローをサブルーチンとして同一チャネル・同一変数で
実行する。ネストは :class:`WorkflowExecutor` を入れ子に呼ぶことで実現し、
``ctx.active_workflow_ids`` の呼び出しスタックで cross-workflow の循環
（A→B→A）を実行時に検出して打ち切る（自己再帰 A→A は保存時 validate_graph が拒否）。

安全側の設計: sessionmaker 未配線 / 対象ワークフロー不在・無効 / 定義不正 / 循環検出の
いずれも「そのサブフローをスキップして親フローを継続」（return None → "out"）とし、
実行中の通話を落とさない。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from millicall.workflows.executor import WorkflowExecutor, register_handler

if TYPE_CHECKING:
    from millicall.workflows.context import ChannelContext

_logger = logging.getLogger(__name__)


@register_handler("call_workflow")
async def handle_call_workflow(node: object, ctx: ChannelContext) -> str | None:
    """別ワークフローをサブルーチンとして実行する。

    親フローの変数・チャネルを共有したまま対象ワークフローを実行し、完了後に
    親フローの "out" 遷移へ戻る。異常時・循環時はスキップして継続する。
    """
    config = node.config  # type: ignore[attr-defined]
    target_id = config.workflow_id

    if ctx.sessionmaker is None:
        _logger.warning("call_workflow: sessionmaker 未配線のためスキップ (id=%s)", target_id)
        return None

    if target_id in ctx.active_workflow_ids:
        _logger.warning(
            "call_workflow: 循環検出のためスキップ (id=%s, stack=%s)",
            target_id,
            sorted(ctx.active_workflow_ids),
        )
        return None

    # 遅延 import で循環 import を回避（models / schema は重い依存）。
    from millicall.models import Workflow
    from millicall.workflows.schema import WorkflowDefinition

    async with ctx.sessionmaker() as session:
        row = await session.get(Workflow, target_id)
        if row is None or not row.enabled:
            _logger.warning(
                "call_workflow: 対象ワークフローが存在しない/無効のためスキップ (id=%s)",
                target_id,
            )
            return None
        definition_json = row.definition_json

    try:
        defn = WorkflowDefinition.model_validate(json.loads(definition_json or "{}"))
    except Exception:  # noqa: BLE001 — 不正定義はスキップして親フロー継続
        _logger.exception("call_workflow: 定義の解析に失敗したためスキップ (id=%s)", target_id)
        return None

    ctx.active_workflow_ids.add(target_id)
    try:
        await WorkflowExecutor(defn, ctx).execute()
    finally:
        ctx.active_workflow_ids.discard(target_id)

    # サブフロー完了後は親フローの "out" へ戻る（サブフローが hangup していれば
    # 親側の executor が ctx.hung_up を見て終了する）。
    return None
